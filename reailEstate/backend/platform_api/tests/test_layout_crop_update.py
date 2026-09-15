"""Isolated crop-update contracts. Written offline; no real DB/provider execution."""

import hashlib
import io
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from PIL import Image
from django.contrib.admin import AdminSite, ModelAdmin
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db import connection
from django.http import HttpResponse
from django.urls import path, reverse
from django.utils import timezone

from platform_api import layout_crop_update as crops
from platform_api import layout_reanalysis
from platform_api.artifact_storage import storage
from platform_api.ingestion_runtime import WorkAlreadyClaimed
from platform_api.layout_crop_update_admin import LayoutCropUpdateAdminMixin, LayoutCropUpdateForm
from platform_api.models import (
    AgentWorkflowRun, AuditEvent, EvidenceDocument, FloorPlan, FloorPlanAsset,
    IngestionRun, LayoutCropDraft, Operation, Organization, OutboxMessage, Project,
    ProjectMerge, ReviewTask,
)

pytestmark = pytest.mark.django_db(transaction=True)


class CropTestSite(AdminSite):
    def get_urls(self):
        return [path("project/<uuid:object_id>/media/", lambda request, object_id: HttpResponse("Dashboard"),
                     name="platform_api_project_media_dashboard"), *super().get_urls()]


class CropTestAdmin(LayoutCropUpdateAdminMixin, ModelAdmin):
    def get_urls(self):
        return self.layout_crop_update_urls() + super().get_urls()


site = CropTestSite(name="crop_edit_test")
site.register(FloorPlan, CropTestAdmin)
urlpatterns = [path("admin/", site.urls)]


def png(size, color):
    with Image.new("RGB", size, color) as image:
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()


@pytest.fixture
def layout(settings, tmp_path, django_user_model, monkeypatch):
    settings.WORKSPACE_DATA_ROOT = tmp_path
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.ROOT_URLCONF = __name__
    settings.FLOOR_PLAN_EXTRACTION_MODE = "script"
    settings.FLOOR_PLAN_VISION_PROFILES = {"pinned": {"provider": "ollama", "model": "offline-only"}}
    settings.FLOOR_PLAN_DEFAULT_VISION_PROFILE = "pinned"
    monkeypatch.setattr(layout_reanalysis, "get_floor_plan_vision_provider",
                        Mock(side_effect=AssertionError("Crop edit called a model")))
    actor = django_user_model.objects.create_superuser(username="crop-editor", password="test-only")
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Crop tests"),
                                     submitted_name="Canonical project")
    original = png((300, 180), "blue")
    current = png((120, 100), "red")
    document = EvidenceDocument.objects.create(
        project=project, uploaded_by=actor, original_name="original.png",
        object_key=storage.save("sources/original.png", ContentFile(original)),
        declared_media_type="image/png", byte_length=len(original), sha256=hashlib.sha256(original).hexdigest(),
        scan_status="clean", retention_until=timezone.now() + timedelta(days=30),
    )
    run = IngestionRun.objects.create(project=project, document=document, status="completed")
    workflow = AgentWorkflowRun.objects.create(project=project, document=document, ingestion_run=run, status="completed")
    source = FloorPlanAsset.objects.create(
        project=project, document=document, workflow_run=workflow, page_number=1, asset_type="page_render",
        object_key=storage.save("images/original.png", ContentFile(original)), byte_length=len(original),
        sha256=hashlib.sha256(original).hexdigest(), pixel_width=300, pixel_height=180, detector_version="test",
    )
    asset = FloorPlanAsset.objects.create(
        project=project, document=document, workflow_run=workflow, page_number=1, asset_type="unit_crop",
        object_key=storage.save("images/current.png", ContentFile(current)), byte_length=len(current),
        sha256=hashlib.sha256(current).hexdigest(), pixel_width=120, pixel_height=100, detector_version="test",
        source_bounds={"sourceAssetId": str(source.pk), "sourceDocumentId": str(document.pk)},
    )
    plan = FloorPlan.objects.create(
        project=project, document=document, asset=asset, workflow_run=workflow, page_number=1, plan_scope="flat",
        plan_name="Immutable unit name", display_name="Reviewer alias", configuration_type="2BHK",
        carpet_area_sqft="650.00", room_count=2, bathroom_count=2, balcony_count=1, orientation="north",
        dimensions=[{"room": "bedroom", "length": 12, "width": 10}], features={"balcony": True},
        confidence=0.91, review_state="needs_boundary_review", extractor_version="old-model",
        raw_extraction={"sourcePlanName": "Original name", "qualityWarning": "Bad old boundary",
                        "cropEvaluation": {"assetId": str(asset.pk)}, "modelConfiguration": "old facts",
                        "boundingBox": {"sourceAssetId": str(source.pk), "sourceDocumentId": str(document.pk), "pageNumber": 1}},
    )
    return plan, actor, source


def edit_args(plan, actor, mode="current"):
    _, _, state, token = crops.prepare_crop_edit(plan.pk, actor, source_mode=mode)
    return {
        "editor_token": token, "expected_asset_id": state["expected_asset_id"],
        "expected_sha256": state["expected_sha256"], "source_sha256": state["source_sha256"],
        "source_mode": mode, "regions": [{"x": 0.05, "y": 0.05, "width": 0.9, "height": 0.9}],
        "rotation_degrees": 0, "confirm": True,
    }


def sibling(plan):
    return FloorPlan.objects.create(
        project=plan.project, document=plan.document, workflow_run=plan.workflow_run, asset=plan.asset,
        page_number=1, plan_scope="flat", plan_name="Sibling sharing old image", extractor_version="old-model",
        configuration_type="1BHK", dimensions=[{"room": "living"}],
    )


def test_saved_identity_immutable_asset_and_extraction_snapshot(layout):
    plan, actor, _ = layout
    other = sibling(plan)
    old_plan = FloorPlan.objects.values().get(pk=plan.pk)
    old_asset = FloorPlanAsset.objects.values().get(pk=plan.asset_id)
    other_before = FloorPlan.objects.values().get(pk=other.pk)
    with storage.open(plan.asset.object_key, "rb") as stream:
        old_bytes = stream.read()
    result = crops.update_layout_crop(plan.pk, actor, **edit_args(plan, actor))
    plan.refresh_from_db()
    assert result["floorPlanId"] == str(plan.pk) == str(old_plan["id"])
    for field in ("plan_name", "display_name", "document_id", "project_id", "workflow_run_id", "page_number"):
        assert getattr(plan, field) == old_plan[field]
    assert plan.asset_id != old_asset["id"]
    assert plan.asset.workflow_run_id != old_asset["workflow_run_id"]
    assert FloorPlanAsset.objects.values().get(pk=old_asset["id"]) == old_asset
    with storage.open(old_asset["object_key"], "rb") as stream:
        assert stream.read() == old_bytes
    assert FloorPlan.objects.values().get(pk=other.pk) == other_before
    assert FloorPlan.objects.count() == 2 and LayoutCropDraft.objects.count() == 0
    for field, value in crops.FACT_DEFAULTS.items():
        assert getattr(plan, field) == value
    raw = plan.raw_extraction
    assert "qualityWarning" not in raw and "cropEvaluation" not in raw and "modelConfiguration" not in raw
    assert raw["sourcePlanName"] == "Original name" and raw["segmentationMethod"] == "manual"
    previous = raw["cropRevisionHistory"][0]["previous"]
    assert previous["assetId"] == str(old_asset["id"])
    assert previous["configuration_type"] == "2BHK" and previous["carpet_area_sqft"] == "650.00"
    assert previous["raw_extraction"]["qualityWarning"] == "Bad old boundary"
    audit = AuditEvent.objects.get(action="floor_plan.crop_updated", entity_id=plan.pk)
    assert audit.metadata["revision"]["previous"] == previous
    job = OutboxMessage.objects.get(pk=result["messageId"])
    assert job.payload["floorPlanId"] == str(plan.pk)
    assert job.payload["assetSha256"] == plan.asset.sha256 and job.payload["assetId"] == str(plan.asset_id)
    assert job.published_at is None and job.attempts == 0
    layout_reanalysis.get_floor_plan_vision_provider.assert_not_called()


def test_crop_update_is_draft_only_until_explicit_commit(layout):
    from platform_api.crop_cart import commit_saved_draft, stage_crop_update

    plan, actor, _ = layout
    before = FloorPlan.objects.values().get(pk=plan.pk)
    asset_count = FloorPlanAsset.objects.count()
    draft = stage_crop_update(plan.pk, actor, **edit_args(plan, actor))
    assert FloorPlan.objects.values().get(pk=plan.pk) == before
    assert FloorPlanAsset.objects.count() == asset_count
    assert not OutboxMessage.objects.exists()
    assert not Operation.objects.exists()
    assert draft.bounds["draftAction"] == "update"
    assert commit_saved_draft(plan.project, actor, draft.pk) == "update"
    plan.refresh_from_db()
    assert plan.asset_id != before["asset_id"]
    assert plan.asset.sha256 == draft.sha256
    assert FloorPlan.objects.count() == 1
    assert OutboxMessage.objects.count() == 1
    assert not LayoutCropDraft.objects.exists()
    layout_reanalysis.get_floor_plan_vision_provider.assert_not_called()


def test_stale_crop_update_draft_is_retained(layout):
    from platform_api.crop_cart import commit_saved_draft, stage_crop_update

    plan, actor, _ = layout
    draft = stage_crop_update(plan.pk, actor, **edit_args(plan, actor))
    plan.display_name = "Changed after staging"
    plan.save()
    with pytest.raises(crops.StaleCropEdit):
        commit_saved_draft(plan.project, actor, draft.pk)
    assert LayoutCropDraft.objects.filter(pk=draft.pk).exists()
    assert not OutboxMessage.objects.exists()


def test_admin_crop_save_stages_without_queueing(layout, client):
    import json

    plan, actor, _ = layout
    client.force_login(actor)
    args = edit_args(plan, actor)
    response = client.post(editor_url(plan), {**args, "regions": json.dumps(args["regions"]), "confirm": "on"})
    assert response.status_code == 302
    assert response.url.endswith("#draft-crop-cart")
    assert LayoutCropDraft.objects.get().bounds["draftAction"] == "update"
    assert not OutboxMessage.objects.exists()


def test_cancels_old_jobs_for_target_only_and_reconciles_scoped_reviews(layout):
    plan, actor, _ = layout
    other = sibling(plan)
    old_job, old_op = layout_reanalysis.queue_layout_evaluation(plan, actor)
    other_job, other_op = layout_reanalysis.queue_layout_evaluation(other, actor)
    boundary = ReviewTask.objects.create(project=plan.project, task_type="layout_quality", resolution={
        "floorPlanId": str(plan.pk), "sourceAssetId": str(plan.asset_id), "reasonCode": "BAD_CROP"})
    duplicate = ReviewTask.objects.create(project=plan.project, task_type="layout_quality", resolution={
        "floorPlanId": str(plan.pk), "sourceAssetId": str(plan.asset_id), "reasonCode": "DUPLICATE"})
    sibling_review = ReviewTask.objects.create(project=plan.project, task_type="floor_plan",
                                               resolution={"floorPlanId": str(other.pk)})
    crops.update_layout_crop(plan.pk, actor, **edit_args(plan, actor))
    for row in (old_job, old_op, other_job, other_op, boundary, duplicate, sibling_review):
        row.refresh_from_db()
    assert old_op.state == "cancelled" and old_job.dead_lettered_at
    assert old_job.last_error_code == "STALE_LAYOUT_ASSET"
    assert other_op.state == "pending" and other_job.dead_lettered_at is None
    assert boundary.state == "resolved" and boundary.resolution["previousAssetId"] == str(plan.asset_id)
    assert duplicate.state == sibling_review.state == "open"
    assert ReviewTask.objects.filter(task_type="floor_plan", state="open", resolution={"floorPlanId": str(plan.pk)}).count() == 1


def test_default_preview_is_current_and_original_is_explicit(layout, monkeypatch):
    plan, actor, source = layout
    render = Mock(side_effect=AssertionError("Saved source should not render the document"))
    monkeypatch.setattr(crops, "render_source_pages", render)
    for mode, expected in (("current", plan.asset), ("original", source)):
        _, _, state, token = crops.prepare_crop_edit(plan.pk, actor, source_mode=mode)
        data = crops.crop_edit_preview(plan.pk, actor, token)
        assert state["source_asset_id"] == str(expected.pk)
        assert hashlib.sha256(data).hexdigest() == expected.sha256
    render.assert_not_called()


def test_missing_original_asset_renders_verified_page_not_current_crop(layout):
    plan, actor, _ = layout
    missing = str(uuid4())
    plan.asset.source_bounds = {"sourceAssetId": missing}
    plan.asset.save()
    plan.raw_extraction["boundingBox"]["sourceAssetId"] = missing
    plan.save()
    _, _, state, token = crops.prepare_crop_edit(plan.pk, actor, source_mode="original")
    assert state["source_asset_id"] is None and (state["width"], state["height"]) == (300, 180)
    assert crops.crop_edit_preview(plan.pk, actor, token)
    with storage.open(plan.document.object_key, "wb") as stream:
        stream.write(b"corrupt original")
    with pytest.raises(ValueError, match="checksum"):
        crops.crop_edit_preview(plan.pk, actor, token)


def test_document_provenance_only_falls_back_to_verified_document(layout):
    plan, actor, _ = layout
    plan.asset.source_bounds = {}
    plan.asset.save()
    plan.raw_extraction = {"boundingBox": {"sourceDocumentId": str(plan.document_id), "pageNumber": 1}}
    plan.save()
    args = edit_args(plan, actor, "original")
    crops.update_layout_crop(plan.pk, actor, **args)
    plan.refresh_from_db()
    assert (plan.asset.pixel_width, plan.asset.pixel_height) == (270, 162)


def test_direct_plan_crop_without_original_asset_uses_document_only_when_requested(layout):
    plan, actor, _ = layout
    plan.asset.asset_type = "plan_crop"
    plan.asset.source_bounds = {}
    plan.asset.save()
    plan.raw_extraction = {"boundingBox": {"sourceDocumentId": str(plan.document_id), "pageNumber": 1}}
    plan.save()
    _, _, current, _ = crops.prepare_crop_edit(plan.pk, actor)
    _, _, original, _ = crops.prepare_crop_edit(plan.pk, actor, source_mode="original")
    assert current["source_asset_id"] == str(plan.asset_id) and current["width"] == 120
    assert original["source_asset_id"] is None and original["width"] == 300


def test_repeat_trim_keeps_linear_history_and_original_ancestry(layout):
    plan, actor, source = layout
    crops.update_layout_crop(plan.pk, actor, **edit_args(plan, actor))
    plan.refresh_from_db()
    first = plan.asset_id
    crops.update_layout_crop(plan.pk, actor, **edit_args(plan, actor))
    plan.refresh_from_db()
    assert plan.raw_extraction["cropRevision"] == 2
    history = plan.raw_extraction["cropRevisionHistory"]
    assert len(history) == 2 and history[1]["previous"]["assetId"] == str(first)
    assert "cropRevisionHistory" not in history[1]["previous"]["raw_extraction"]
    _, _, state, _ = crops.prepare_crop_edit(plan.pk, actor, source_mode="original")
    assert state["source_asset_id"] == str(source.pk)


@pytest.mark.parametrize("field,value", [("expected_asset_id", str(uuid4())), ("expected_sha256", "0" * 64),
                                        ("source_sha256", "1" * 64), ("source_mode", "original")])
def test_hidden_version_and_source_tampering_rejected(layout, field, value):
    plan, actor, _ = layout
    args = edit_args(plan, actor)
    args[field] = value
    with pytest.raises(crops.StaleCropEdit):
        crops.update_layout_crop(plan.pk, actor, **args)
    assert FloorPlanAsset.objects.count() == 2 and OutboxMessage.objects.count() == 0


def test_second_post_with_old_editor_is_rejected(layout):
    plan, actor, _ = layout
    args = edit_args(plan, actor)
    crops.update_layout_crop(plan.pk, actor, **args)
    with pytest.raises(crops.StaleCropEdit):
        crops.update_layout_crop(plan.pk, actor, **args)
    assert FloorPlanAsset.objects.count() == 3
    assert AuditEvent.objects.filter(action="floor_plan.crop_updated").count() == 1


def test_cancelled_cached_payload_cannot_run_inference_or_overwrite_new_crop(layout):
    plan, actor, _ = layout
    job, operation = layout_reanalysis.queue_layout_evaluation(plan, actor)
    payload = dict(job.payload)
    crops.update_layout_crop(plan.pk, actor, **edit_args(plan, actor))
    before = FloorPlan.objects.values().get(pk=plan.pk)
    layout_reanalysis.evaluate_layout_crop(payload)
    assert FloorPlan.objects.values().get(pk=plan.pk) == before
    operation.refresh_from_db()
    assert operation.state == "cancelled"
    layout_reanalysis.get_floor_plan_vision_provider.assert_not_called()


@pytest.mark.parametrize("event", ["source.page_evaluation.requested", "floor_plan.units_evaluation.requested"])
def test_old_page_or_units_job_cancelled_only_for_target(layout, event):
    plan, actor, _ = layout
    other = sibling(plan)
    jobs = []
    for target in (plan, other):
        operation = Operation.objects.create(owner=actor, kind="test", state="pending")
        jobs.append(OutboxMessage.objects.create(event_type=event, correlation_id=uuid4(), payload={
            "floorPlanId": str(target.pk), "assetId": str(target.asset_id),
            "assetSha256": target.asset.sha256, "operationId": str(operation.pk),
        }))
    crops.update_layout_crop(plan.pk, actor, **edit_args(plan, actor))
    assert OutboxMessage.objects.get(pk=jobs[0].pk).dead_lettered_at
    assert OutboxMessage.objects.get(pk=jobs[1].pk).dead_lettered_at is None


def test_shared_operation_cannot_cancel_a_sibling(layout):
    plan, actor, _ = layout
    other = sibling(plan)
    job, operation = layout_reanalysis.queue_layout_evaluation(plan, actor)
    OutboxMessage.objects.create(event_type=job.event_type, correlation_id=uuid4(),
                                 payload=job.payload | {"floorPlanId": str(other.pk)})
    with pytest.raises(ValueError, match="shared"):
        crops.update_layout_crop(plan.pk, actor, **edit_args(plan, actor))
    operation.refresh_from_db()
    assert operation.state == "pending"
    assert FloorPlanAsset.objects.count() == 2


def test_stale_evaluation_during_io_cannot_overwrite_newer_state(layout, monkeypatch):
    plan, actor, _ = layout
    args = edit_args(plan, actor)
    render = crops._render_crop

    def concurrent_change(*values):
        assert not connection.in_atomic_block
        result = render(*values)
        FloorPlan.objects.filter(pk=plan.pk).update(configuration_type="newer evaluation", updated_at=timezone.now())
        return result

    monkeypatch.setattr(crops, "_render_crop", concurrent_change)
    with pytest.raises(crops.StaleCropEdit):
        crops.update_layout_crop(plan.pk, actor, **args)
    plan.refresh_from_db()
    assert plan.configuration_type == "newer evaluation"
    assert FloorPlanAsset.objects.count() == 2 and OutboxMessage.objects.count() == 0


def test_io_outside_transaction_and_queue_inside_short_transaction(layout, monkeypatch):
    plan, actor, _ = layout
    args = edit_args(plan, actor)
    save, queue = storage.save, layout_reanalysis.queue_layout_evaluation

    def checked_save(*values, **kwargs):
        assert not connection.in_atomic_block
        return save(*values, **kwargs)

    def checked_queue(*values, **kwargs):
        assert connection.in_atomic_block
        return queue(*values, **kwargs)

    monkeypatch.setattr(storage, "save", checked_save)
    monkeypatch.setattr(layout_reanalysis, "queue_layout_evaluation", checked_queue)
    crops.update_layout_crop(plan.pk, actor, **args)


def test_busy_worker_lock_blocks_edit_without_persistence(layout, monkeypatch):
    plan, actor, _ = layout
    args = edit_args(plan, actor)
    monkeypatch.setattr(crops, "work_lock", Mock(side_effect=WorkAlreadyClaimed("busy")))
    with pytest.raises(WorkAlreadyClaimed):
        crops.update_layout_crop(plan.pk, actor, **args)
    assert FloorPlanAsset.objects.count() == 2


def test_queue_failure_rolls_back_and_deletes_only_new_uncommitted_file(layout, monkeypatch):
    plan, actor, _ = layout
    args = edit_args(plan, actor)
    old = plan.asset_id
    deletion = Mock(wraps=storage.delete)
    monkeypatch.setattr(storage, "delete", deletion)
    monkeypatch.setattr(layout_reanalysis, "queue_layout_evaluation", Mock(side_effect=ValueError("No profile")))
    with pytest.raises(ValueError, match="No profile"):
        crops.update_layout_crop(plan.pk, actor, **args)
    plan.refresh_from_db()
    assert plan.asset_id == old and plan.configuration_type == "2BHK"
    assert FloorPlanAsset.objects.count() == 2 and IngestionRun.objects.count() == 1
    assert AuditEvent.objects.count() == 0 and ReviewTask.objects.count() == 0
    deletion.assert_called_once()
    assert "/crop-revisions/" in deletion.call_args.args[0]
    assert storage.exists(plan.asset.object_key)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_full_range_selection_and_right_angle_rotations(layout, rotation):
    plan, actor, _ = layout
    args = edit_args(plan, actor)
    args.update(regions=[{"x": 0, "y": 0, "width": 1, "height": 1}], rotation_degrees=rotation)
    crops.update_layout_crop(plan.pk, actor, **args)
    plan.refresh_from_db()
    expected_size = (100, 120) if rotation % 180 else (120, 100)
    assert (plan.asset.pixel_width, plan.asset.pixel_height) == expected_size


@pytest.mark.parametrize("regions", [None, [], [{}, {}], [{}],
    [{"x": True, "y": 0, "width": 1, "height": 1}],
    [{"x": 0, "y": 0, "width": float("nan"), "height": 1}],
    [{"x": 0, "y": 0, "width": float("inf"), "height": 1}],
    [{"x": 0, "y": 0, "width": 0, "height": 1}],
    [{"x": -0.01, "y": 0, "width": 1, "height": 1}],
    [{"x": 0.1, "y": 0, "width": 0.91, "height": 1}],
    [{"x": "0", "y": 0, "width": 1, "height": 1}],
])
def test_invalid_rectangles_fail_closed(regions):
    with pytest.raises(ValueError):
        crops.validate_rectangle(regions)


@pytest.mark.parametrize("rotation", [45, -90, 360, True, 90.0, "90"])
def test_invalid_rotations(rotation):
    with pytest.raises(ValueError):
        crops.validate_rotation(rotation)


def test_confirmation_must_be_explicit(layout):
    plan, actor, _ = layout
    args = edit_args(plan, actor)
    args["confirm"] = False
    with pytest.raises(ValueError, match="Confirm"):
        crops.update_layout_crop(plan.pk, actor, **args)
    assert not LayoutCropUpdateForm({}).is_valid()


@pytest.mark.parametrize("active,staff,permissions", [
    (False, True, ["change_floorplan", "view_project"]),
    (True, False, ["change_floorplan", "view_project"]),
    (True, True, ["view_project"]), (True, True, ["change_floorplan"]),
])
def test_staff_model_and_project_permissions_all_required(layout, django_user_model, active, staff, permissions):
    plan, _, _ = layout
    actor = django_user_model.objects.create_user(username="limited", is_active=active, is_staff=staff)
    actor.user_permissions.add(*Permission.objects.filter(content_type__app_label="platform_api", codename__in=permissions))
    with pytest.raises(PermissionDenied):
        crops.prepare_crop_edit(plan.pk, actor)


def test_non_superuser_with_both_permissions_can_edit(layout, django_user_model):
    plan, _, _ = layout
    actor = django_user_model.objects.create_user(username="reviewer", is_staff=True)
    actor.user_permissions.add(*Permission.objects.filter(content_type__app_label="platform_api",
                                                          codename__in=["change_floorplan", "view_project"]))
    crops.update_layout_crop(plan.pk, actor, **edit_args(plan, actor))


@pytest.mark.parametrize("state", ["removed_by_user", "replaced_by_user", "superseded_page_candidate"])
def test_hidden_layouts_blocked(layout, state):
    plan, actor, _ = layout
    plan.review_state = state
    plan.save()
    with pytest.raises(ValueError):
        crops.prepare_crop_edit(plan.pk, actor)


def test_canonical_merged_family_source_keeps_plan_identity_project_and_document(layout):
    plan, actor, source = layout
    old_project_id, old_document_id = plan.project_id, plan.document_id
    target = Project.objects.create(organization=plan.project.organization, submitted_name="Merged canonical")
    ProjectMerge.objects.create(source_project=plan.project, target_project=target, reason_code="TEST")
    plan.project.status = "merged"
    plan.project.save()
    args = edit_args(plan, actor, "original")
    result = crops.update_layout_crop(plan.pk, actor, **args)
    plan.refresh_from_db()
    assert result["projectId"] == str(target.pk)
    assert plan.project_id == old_project_id and plan.document_id == old_document_id
    assert plan.asset.project_id == target.pk
    assert plan.asset.source_bounds["sourceAssetId"] == str(source.pk)
    target.status = "retired"
    target.save()
    with pytest.raises(ValueError):
        crops.prepare_crop_edit(plan.pk, actor)


def test_cross_organization_merge_and_foreign_source_provenance_blocked(layout):
    plan, actor, source = layout
    foreign = Project.objects.create(organization=Organization.objects.create(legal_name="Foreign"), submitted_name="Foreign")
    source.project = foreign
    source.save()
    with pytest.raises(ValueError, match="family"):
        crops.prepare_crop_edit(plan.pk, actor, source_mode="original")
    # Current source never jumps to (or reads) the invalid original reference.
    crops.prepare_crop_edit(plan.pk, actor)
    ProjectMerge.objects.create(source_project=plan.project, target_project=foreign, reason_code="BAD_MERGE")
    with pytest.raises(ValueError):
        crops.prepare_crop_edit(plan.pk, actor)


def editor_url(plan):
    return reverse("admin:platform_api_floorplan_crop_update", args=[plan.pk], current_app=site.name)


def test_admin_preview_auth_private_headers_and_read_only_get(layout, client):
    plan, actor, _ = layout
    _, _, _, token = crops.prepare_crop_edit(plan.pk, actor)
    url = reverse("admin:platform_api_floorplan_crop_update_preview", args=[plan.pk], current_app=site.name)
    assert client.get(url, {"token": token}).status_code == 302
    client.force_login(actor)
    response = client.get(url, {"token": token})
    assert response.status_code == 200
    assert response["Cache-Control"] == "private, no-store"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert hashlib.sha256(response.content).hexdigest() == plan.asset.sha256
    assert client.post(url, {"token": token}).status_code == 405
    assert FloorPlanAsset.objects.count() == 2 and AuditEvent.objects.count() == 0
    assert OutboxMessage.objects.count() == 0 and LayoutCropDraft.objects.count() == 0
    response = client.get(editor_url(plan))
    assert response.status_code == 200
    assert response.context_data["source_mode"] == "current"
    assert response.context_data["crop_config"]["width"] == 120
    assert response.context_data["form"].initial["regions"] == []


def test_preview_token_is_actor_bound_and_tamper_proof(layout, django_user_model):
    plan, actor, _ = layout
    _, _, _, token = crops.prepare_crop_edit(plan.pk, actor)
    other = django_user_model.objects.create_superuser(username="other", password="test-only")
    with pytest.raises(PermissionDenied):
        crops.crop_edit_preview(plan.pk, other, token)
    with pytest.raises(crops.StaleCropEdit):
        crops.crop_edit_preview(plan.pk, actor, token + "modified")


def test_removed_or_retired_target_cannot_reuse_existing_preview(layout, client):
    plan, actor, _ = layout
    _, _, _, token = crops.prepare_crop_edit(plan.pk, actor)
    url = reverse("admin:platform_api_floorplan_crop_update_preview", args=[plan.pk], current_app=site.name)
    client.force_login(actor)
    plan.project.status = "retired"
    plan.project.save()
    assert client.get(url, {"token": token}).status_code == 404
    assert FloorPlanAsset.objects.count() == 2


def test_asset_and_document_checksum_changes_reject_preview(layout):
    plan, actor, _ = layout
    _, _, _, token = crops.prepare_crop_edit(plan.pk, actor)
    plan.asset.sha256 = "f" * 64
    plan.asset.save()
    with pytest.raises(crops.StaleCropEdit):
        crops.crop_edit_preview(plan.pk, actor, token)
    plan.asset.sha256 = hashlib.sha256(png((120, 100), "red")).hexdigest()
    plan.asset.save()
    _, _, _, token = crops.prepare_crop_edit(plan.pk, actor)
    plan.document.sha256 = "e" * 64
    plan.document.save()
    with pytest.raises(crops.StaleCropEdit):
        crops.crop_edit_preview(plan.pk, actor, token)


def test_stale_admin_post_returns_conflict_without_rebasing(layout, client):
    import json

    plan, actor, _ = layout
    client.force_login(actor)
    args = edit_args(plan, actor, "original")
    crops.update_layout_crop(plan.pk, actor, **args)
    posted = {**args, "regions": json.dumps(args["regions"]), "confirm": "on"}
    response = client.post(editor_url(plan), posted)
    assert response.status_code == 409
    assert response.context_data["source_mode"] == "original"
    assert response.context_data["preview_url"] is None
    assert "source_mode=original" in response.context_data["reload_url"]
    assert FloorPlanAsset.objects.count() == 3


def test_admin_post_requires_csrf(layout):
    from django.test import Client

    plan, actor, _ = layout
    client = Client(enforce_csrf_checks=True)
    client.force_login(actor)
    assert client.post(editor_url(plan), {}).status_code == 403