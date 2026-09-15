"""Offline regression coverage; all model responses are mocked."""

import hashlib
import io
from datetime import timedelta
from unittest.mock import Mock

import pytest
from PIL import Image
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db import connection, transaction
from django.utils import timezone

from platform_api import floor_plan_layout_jobs as jobs
from platform_api.artifact_storage import storage
from platform_api.floor_plan_vision import resolve_ai_vision_selection
from platform_api.ingestion_runtime import work_lock
from platform_api.models import (
    AgentWorkflowRun, AuditEvent, EvidenceDocument, FloorPlan, FloorPlanAsset,
    InboxMessage, IngestionRun, Operation, Organization, OutboxMessage, Project,
)
from platform_api.unit_segmentation import UnitRegion, replace_unit_segments
from platform_api.workers import process_outbox_message


# Real transaction boundaries let the mock detect accidentally transactional AI.
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def source(settings, tmp_path, django_user_model):
    settings.WORKSPACE_DATA_ROOT = tmp_path
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.FLOOR_PLAN_EXTRACTION_MODE = "script"
    settings.FLOOR_PLAN_VISION_PROFILES = {
        "pinned": {"provider": "ollama", "model": "original-model"},
        "other": {"provider": "ollama", "model": "other-model"},
    }
    settings.FLOOR_PLAN_DEFAULT_VISION_PROFILE = "pinned"
    actor = django_user_model.objects.create_superuser(username="floor-layout-reviewer", password="test-only")
    project = Project.objects.create(
        organization=Organization.objects.create(legal_name="Queued layouts"), submitted_name="Full floor",
    )
    with Image.new("RGB", (200, 100), "white") as image:
        image.paste("red", (0, 0, 100, 100))
        image.paste("blue", (100, 0, 200, 100))
        output = io.BytesIO()
        image.save(output, format="PNG")
        data = output.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    document = EvidenceDocument.objects.create(
        project=project, uploaded_by=actor, original_name="source.png",
        object_key=storage.save("sources/source.png", ContentFile(data)),
        declared_media_type="image/png", byte_length=len(data), sha256=digest,
        scan_status="clean", retention_until=timezone.now() + timedelta(days=7),
    )
    run = IngestionRun.objects.create(project=project, document=document, status="completed")
    workflow = AgentWorkflowRun.objects.create(
        project=project, document=document, ingestion_run=run, status="completed",
    )
    asset = FloorPlanAsset.objects.create(
        project=project, document=document, workflow_run=workflow, page_number=1,
        asset_type="page_render", object_key=storage.save("images/floor.png", ContentFile(data)),
        byte_length=len(data), sha256=digest, pixel_width=200, pixel_height=100, detector_version="test",
    )
    plan = FloorPlan.objects.create(
        project=project, document=document, workflow_run=workflow, asset=asset, page_number=1,
        plan_scope="floor", plan_name="Full floor", review_state="gen_with_ai_needs_review",
        raw_extraction={"sourcePlanName": "Original floor"}, extractor_version="test",
    )
    return plan, actor


@pytest.fixture
def provider(monkeypatch):
    regions = [
        UnitRegion("Unit 1", 0.05, 0.1, 0.4, 0.8, 0.9, "Entrance 1", ["1ST FLOOR"]),
        UnitRegion("Unit 2", 0.55, 0.1, 0.4, 0.8, 0.9, "Entrance 2", ["1ST FLOOR"]),
    ]
    metadata = {"pageType": "multi_unit_floor_plan", "unitCount": 2, "visualEvidence": "Two entrances"}
    mock = Mock(spec=["analyze_unit_regions", "close"])

    def analyze(*_args, **_kwargs):
        assert not connection.in_atomic_block, "Model call must be outside database transactions"
        return regions, metadata

    mock.analyze_unit_regions.side_effect = analyze
    factory = Mock(return_value=mock)
    monkeypatch.setattr(jobs, "get_floor_plan_vision_provider", factory)
    return mock, factory, regions, metadata


def operation_for(message):
    return Operation.objects.get(pk=message.payload["operationId"])


def test_queue_inside_source_worker_lock_is_durable_deduplicated_and_pinned(source, provider, monkeypatch):
    plan, actor = source
    _, factory, _, _ = provider
    selection = resolve_ai_vision_selection()
    monkeypatch.setattr(jobs, "work_lock", Mock(side_effect=AssertionError("Nested work lock")))
    monkeypatch.setattr(storage, "open", Mock(side_effect=AssertionError("Queue read an image")))
    with work_lock("document", plan.document_id), transaction.atomic():
        message = jobs.queue_floor_plan_layouts(plan, actor, vision_selection=selection)
        repeated = jobs.queue_floor_plan_layouts(plan, actor, vision_profile_id="other")
    assert repeated.pk == message.pk
    assert message.payload["sourcePlanId"] == message.payload["floorPlanId"] == str(plan.pk)
    assert message.payload["assetSha256"] == plan.asset.sha256
    assert message.payload["visionSelection"] == selection
    assert operation_for(message).owner_id == actor.pk
    assert OutboxMessage.objects.count() == Operation.objects.count() == 1
    assert AuditEvent.objects.filter(action="floor_plan.units_evaluation_queued").count() == 1
    factory.assert_not_called()


@pytest.mark.parametrize("active,staff,permission", [(False, True, True), (True, False, True), (True, True, False)])
def test_queue_requires_active_staff_and_floor_plan_permission(source, django_user_model, active, staff, permission):
    plan, _ = source
    actor = django_user_model.objects.create_user(username="limited", is_active=active, is_staff=staff)
    if permission:
        actor.user_permissions.add(Permission.objects.get(
            content_type__app_label="platform_api", codename="change_floorplan",
        ))
    with pytest.raises(PermissionDenied):
        jobs.queue_floor_plan_layouts(plan, actor)
    assert not OutboxMessage.objects.exists()


def test_queue_accepts_staff_with_model_change_permission(source, django_user_model):
    plan, _ = source
    actor = django_user_model.objects.create_user(username="editor", is_staff=True)
    actor.user_permissions.add(Permission.objects.get(
        content_type__app_label="platform_api", codename="change_floorplan",
    ))
    assert jobs.queue_floor_plan_layouts(plan, actor).event_type == jobs.UNITS_EVENT


@pytest.mark.parametrize("state", sorted(jobs.HIDDEN_STATES))
def test_queue_reloads_and_rejects_hidden_sources(source, state):
    plan, actor = source
    FloorPlan.objects.filter(pk=plan.pk).update(review_state=state)
    with pytest.raises(ValueError, match="FLOOR_PLAN_SOURCE_HIDDEN"):
        jobs.queue_floor_plan_layouts(plan, actor)
    assert not Operation.objects.exists()


@pytest.mark.parametrize("kind", ["retired", "merged", "unclean", "not_ready", "unit_crop", "flat", "no_asset"])
def test_ineligible_sources_never_queue(source, kind):
    plan, actor = source
    if kind in {"retired", "merged"}:
        Project.objects.filter(pk=plan.project_id).update(status=kind)
    elif kind == "unclean":
        EvidenceDocument.objects.filter(pk=plan.document_id).update(scan_status="pending")
    elif kind == "not_ready":
        FloorPlanAsset.objects.filter(pk=plan.asset_id).update(status="failed")
    elif kind == "unit_crop":
        FloorPlanAsset.objects.filter(pk=plan.asset_id).update(asset_type="unit_crop")
    else:
        FloorPlan.objects.filter(pk=plan.pk).update(**({"asset": None} if kind == "no_asset" else {"plan_scope": "flat"}))
    with pytest.raises(ValueError):
        jobs.queue_floor_plan_layouts(plan, actor)
    assert not OutboxMessage.objects.exists()


@pytest.mark.parametrize("selection", [{}, {"profileId": "pinned"}, {"profileId": "rules-only"}])
def test_untrusted_or_incomplete_selection_rejected(source, selection):
    plan, actor = source
    with pytest.raises(ValueError):
        jobs.queue_floor_plan_layouts(plan, actor, vision_selection=selection)
    assert not Operation.objects.exists()


def test_default_change_does_not_change_parent_or_crop_pins_and_replay_is_noop(source, provider, settings):
    plan, actor = source
    mock, factory, _, _ = provider
    before = FloorPlan.objects.filter(pk=plan.pk).values().get()
    message = jobs.queue_floor_plan_layouts(plan, actor)
    pin = message.payload["visionSelection"]
    settings.FLOOR_PLAN_DEFAULT_VISION_PROFILE = "other"
    assert process_outbox_message(message.pk) == "processed"
    operation = operation_for(message)
    assert operation.state == "completed" and operation.result["sourceRetained"] is True
    assert operation.result["unitCount"] == 2
    crops = FloorPlan.objects.filter(pk__in=operation.result["planIds"])
    assert crops.count() == 2
    assert all(crop.plan_scope == "flat" and crop.asset.asset_type == "unit_crop" for crop in crops)
    crop_jobs = OutboxMessage.objects.filter(event_type="floor_plan.crop_evaluation.requested")
    assert crop_jobs.count() == 2
    assert all(job.payload["visionSelection"] == pin for job in crop_jobs)
    assert all(operation_for(job).owner_id == actor.pk for job in crop_jobs)
    assert FloorPlan.objects.filter(pk=plan.pk).values().get() == before
    assert not OutboxMessage.objects.filter(event_type="document.processing.requested").exists()
    factory.assert_called_once_with(pin)
    mock.close.assert_called_once()
    assert jobs.evaluate_floor_plan_layouts(message.payload).pk == operation.pk
    assert jobs.queue_floor_plan_layouts(plan, actor, retry=True).pk == message.pk
    assert process_outbox_message(message.pk) == "already_processed"
    assert InboxMessage.objects.filter(message_id=message.pk).count() == 1
    assert OutboxMessage.objects.count() == 3
    mock.analyze_unit_regions.assert_called_once()


@pytest.mark.parametrize("during_model", [False, True])
@pytest.mark.parametrize("state", ["cancelled", *sorted(jobs.HIDDEN_STATES)])
def test_cancellation_and_hidden_records_win_before_model_or_persistence(source, provider, during_model, state):
    plan, actor = source
    mock, factory, regions, metadata = provider
    message = jobs.queue_floor_plan_layouts(plan, actor)

    def stop():
        if state == "cancelled":
            Operation.objects.filter(pk=message.payload["operationId"]).update(state=state)
        else:
            FloorPlan.objects.filter(pk=plan.pk).update(review_state=state)

    if during_model:
        def analyze(*_args):
            assert not connection.in_atomic_block
            stop()
            return regions, metadata
        mock.analyze_unit_regions.side_effect = analyze
    else:
        stop()
    assert jobs.evaluate_floor_plan_layouts(message.payload).state == "cancelled"
    assert FloorPlan.objects.count() == FloorPlanAsset.objects.count() == OutboxMessage.objects.count() == 1
    if during_model:
        mock.close.assert_called_once()
    else:
        factory.assert_not_called()


@pytest.mark.parametrize("during_model", [False, True])
def test_checksum_change_is_rejected_before_model_and_before_persistence(source, provider, during_model):
    plan, actor = source
    mock, factory, regions, metadata = provider
    message = jobs.queue_floor_plan_layouts(plan, actor)

    def change_checksum():
        FloorPlanAsset.objects.filter(pk=plan.asset_id).update(sha256="0" * 64)

    if during_model:
        def analyze(*_args):
            change_checksum()
            return regions, metadata
        mock.analyze_unit_regions.side_effect = analyze
    else:
        change_checksum()
    with pytest.raises(ValueError, match="STALE_FLOOR_PLAN_SOURCE"):
        jobs.evaluate_floor_plan_layouts(message.payload)
    assert operation_for(message).state == "failed"
    assert FloorPlan.objects.count() == OutboxMessage.objects.count() == 1
    if during_model:
        mock.close.assert_called_once()
    else:
        factory.assert_not_called()


def test_corrupt_image_bytes_rejected_before_provider(source, provider):
    plan, actor = source
    _, factory, _, _ = provider
    message = jobs.queue_floor_plan_layouts(plan, actor)
    with storage.open(plan.asset.object_key, "wb") as stream:
        stream.write(b"corrupt")
    with pytest.raises(ValueError, match="IMAGE_INTEGRITY_MISMATCH"):
        jobs.evaluate_floor_plan_layouts(message.payload)
    factory.assert_not_called()


def test_changed_profile_fails_closed_instead_of_using_current_config(source, provider, settings):
    plan, actor = source
    _, factory, _, _ = provider
    message = jobs.queue_floor_plan_layouts(plan, actor)
    settings.FLOOR_PLAN_VISION_PROFILES = {"pinned": {"provider": "ollama", "model": "replacement"}}
    with pytest.raises(ValueError, match="STALE_VISION_SELECTION"):
        jobs.evaluate_floor_plan_layouts(message.payload)
    factory.assert_not_called()


def test_committed_operation_replays_after_missing_outbox_ack_without_new_crops(source, provider):
    plan, actor = source
    mock, _, _, _ = provider
    message = jobs.queue_floor_plan_layouts(plan, actor)
    operation = jobs.evaluate_floor_plan_layouts(message.payload)
    plan_ids = operation.result["planIds"]
    assert not InboxMessage.objects.filter(message_id=message.pk).exists()
    assert OutboxMessage.objects.get(pk=message.pk).published_at is None
    assert process_outbox_message(message.pk) == "processed"
    assert operation_for(message).result["planIds"] == plan_ids
    assert OutboxMessage.objects.count() == 3
    mock.analyze_unit_regions.assert_called_once()


def test_source_id_and_checksum_form_dedup_identity(source):
    plan, actor = source
    original = jobs.queue_floor_plan_layouts(plan, actor)
    sibling = FloorPlan.objects.create(
        project=plan.project, document=plan.document, workflow_run=plan.workflow_run,
        asset=plan.asset, page_number=1, plan_name="Another source record",
        plan_scope="floor", extractor_version="test",
    )
    assert jobs.queue_floor_plan_layouts(sibling, actor).pk != original.pk
    FloorPlanAsset.objects.filter(pk=plan.asset_id).update(sha256="1" * 64)
    changed = jobs.queue_floor_plan_layouts(plan, actor)
    assert changed.pk != original.pk
    assert changed.payload["assetSha256"] == "1" * 64
    assert OutboxMessage.objects.count() == 3


def test_model_failure_does_not_overwrite_concurrent_cancellation(source, provider):
    plan, actor = source
    mock, _, _, _ = provider
    message = jobs.queue_floor_plan_layouts(plan, actor)

    def cancel_and_fail(*_args):
        Operation.objects.filter(pk=message.payload["operationId"]).update(state="cancelled")
        raise RuntimeError("cancelled while awaiting provider")

    mock.analyze_unit_regions.side_effect = cancel_and_fail
    with pytest.raises(RuntimeError):
        jobs.evaluate_floor_plan_layouts(message.payload)
    assert operation_for(message).state == "cancelled"
    assert FloorPlan.objects.count() == 1
    mock.close.assert_called_once()


def test_failed_pending_job_deduplicates_and_dead_letter_retry_is_explicit(source, provider):
    plan, actor = source
    mock, _, regions, metadata = provider
    message = jobs.queue_floor_plan_layouts(plan, actor)
    mock.analyze_unit_regions.side_effect = RuntimeError("provider offline")
    with pytest.raises(RuntimeError):
        jobs.evaluate_floor_plan_layouts(message.payload)
    mock.close.assert_called_once()
    assert jobs.queue_floor_plan_layouts(plan, actor).pk == message.pk
    assert operation_for(message).state == "failed"
    OutboxMessage.objects.filter(pk=message.pk).update(
        attempts=5, dead_lettered_at=timezone.now(), available_after=timezone.now() + timedelta(days=1),
        last_error_code="RuntimeError",
    )
    assert jobs.queue_floor_plan_layouts(plan, actor).dead_lettered_at is not None
    retried = jobs.queue_floor_plan_layouts(plan, actor, vision_profile_id="other", retry=True)
    assert retried.pk == message.pk and retried.dead_lettered_at is None and retried.available_after is None
    assert retried.attempts == 0 and retried.last_error_code == ""
    assert retried.payload["visionSelection"] == message.payload["visionSelection"]
    assert operation_for(message).state == "pending"
    mock.analyze_unit_regions.side_effect = None
    mock.analyze_unit_regions.return_value = regions, metadata
    assert process_outbox_message(message.pk) == "processed"
    assert operation_for(message).state == "completed"


def test_cancelled_dead_letter_is_not_rearmed_even_after_restore(source):
    plan, actor = source
    message = jobs.queue_floor_plan_layouts(plan, actor)
    Operation.objects.filter(pk=message.payload["operationId"]).update(state="cancelled")
    OutboxMessage.objects.filter(pk=message.pk).update(dead_lettered_at=timezone.now())
    assert jobs.queue_floor_plan_layouts(plan, actor, retry=True).dead_lettered_at is not None
    assert operation_for(message).state == "cancelled"


def test_crop_enqueue_failure_rolls_back_replacement_jobs_completion_and_files(source, provider, monkeypatch):
    plan, actor = source
    mock, _, regions, _ = provider
    old_plans, old_workflow = replace_unit_segments(plan, regions, "ai", actor=actor)
    old_ids = {crop.pk for crop in old_plans}
    old_keys = [crop.asset.object_key for crop in old_plans]
    message = jobs.queue_floor_plan_layouts(plan, actor)
    real_queue = jobs.queue_layout_evaluation
    attempted_keys = []

    def fail_second(crop, **kwargs):
        assert connection.in_atomic_block
        attempted_keys.append(crop.asset.object_key)
        if len(attempted_keys) == 2:
            raise RuntimeError("crop enqueue failed")
        return real_queue(crop, **kwargs)

    monkeypatch.setattr(jobs, "queue_layout_evaluation", fail_second)
    with pytest.raises(RuntimeError, match="crop enqueue failed"):
        jobs.evaluate_floor_plan_layouts(message.payload)
    assert set(FloorPlan.objects.exclude(pk=plan.pk).values_list("pk", flat=True)) == old_ids
    assert AgentWorkflowRun.objects.filter(pk=old_workflow.pk).exists()
    assert all(storage.exists(key) for key in old_keys)
    assert not any(storage.exists(key) for key in attempted_keys)
    assert OutboxMessage.objects.count() == Operation.objects.count() == 1
    assert operation_for(message).state == "failed"
    mock.close.assert_called_once()


@pytest.mark.parametrize("invalid", ["empty", "overlap", "non_plan", "wrong_count"])
def test_invalid_model_regions_never_replace_existing_source(source, provider, invalid):
    plan, actor = source
    mock, _, regions, metadata = provider
    if invalid == "empty":
        regions = []
    elif invalid == "overlap":
        regions = [regions[0], regions[0]]
    elif invalid == "non_plan":
        metadata = {**metadata, "pageType": "non_plan"}
    else:
        metadata = {**metadata, "unitCount": 3}
    mock.analyze_unit_regions.side_effect = None
    mock.analyze_unit_regions.return_value = regions, metadata
    message = jobs.queue_floor_plan_layouts(plan, actor)
    with pytest.raises(ValueError):
        jobs.evaluate_floor_plan_layouts(message.payload)
    assert FloorPlan.objects.count() == FloorPlanAsset.objects.count() == 1
    assert not OutboxMessage.objects.filter(event_type="floor_plan.crop_evaluation.requested").exists()
    mock.close.assert_called_once()