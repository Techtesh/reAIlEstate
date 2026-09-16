import hashlib
import io
import json
from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from PIL import Image
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import RequestFactory
from django.utils import timezone

from platform_api.admin import ProjectAdmin
from platform_api.artifact_storage import storage
from platform_api.crop_cart import attach_drafts, discard_drafts, stage_crops
from platform_api.models import (
    AgentWorkflowRun, AuditEvent, EvidenceDocument, FloorPlan, FloorPlanAsset,
    IngestionRun, LayoutCropDraft, Organization, OutboxMessage, Project,
)

REGION = {"x": .1, "y": .1, "width": .4, "height": .5, "label": "Unit 01"}


@pytest.fixture
def source(tmp_path, settings, monkeypatch):
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.WORKSPACE_DATA_ROOT = tmp_path
    from platform_api import floor_plan_vision
    monkeypatch.setattr(floor_plan_vision, "resolve_ai_vision_selection", lambda _: {"profileId": "test"})
    owner = get_user_model().objects.create_superuser(username="crop-owner", password="test-only")
    org = Organization.objects.create(legal_name="Crop tests")
    project = Project.objects.create(organization=org, submitted_name="Crop project")
    image = Image.new("RGB", (400, 300), "blue")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    data = buffer.getvalue()
    doc = EvidenceDocument.objects.create(project=project, original_name="source.png",
        declared_media_type="image/png", object_key=f"source/{uuid4()}.png", byte_length=len(data),
        sha256=hashlib.sha256(data).hexdigest(), scan_status="clean", status="needs_review",
        uploaded_by=owner, retention_until=timezone.now() + timedelta(days=30))
    run = IngestionRun.objects.create(document=doc, project=project, status="completed")
    workflow = AgentWorkflowRun.objects.create(document=doc, project=project, ingestion_run=run, status="completed")
    asset = FloorPlanAsset.objects.create(project=project, document=doc, workflow_run=workflow,
        page_number=1, asset_type="page_render", object_key=storage.save(f"sources/{uuid4()}.png", ContentFile(data)),
        byte_length=len(data), sha256=hashlib.sha256(data).hexdigest(), pixel_width=400, pixel_height=300,
        detector_version="test", source_bounds={"category": "layouts"})
    plan = FloorPlan.objects.create(project=project, document=doc, workflow_run=workflow,
        asset=asset, page_number=1, plan_scope="flat", plan_name="Original unit", extractor_version="test")
    return project, plan, owner


@pytest.mark.django_db
def test_save_only_stages_drafts(source):
    project, plan, actor = source
    counts = (FloorPlan.objects.count(), FloorPlanAsset.objects.count(), IngestionRun.objects.count())
    result = stage_crops(plan, [REGION], actor)
    assert result["state"] == "draft" and result["planIds"] == []
    assert LayoutCropDraft.objects.filter(owner=actor).count() == 1
    assert not OutboxMessage.objects.exists()
    assert counts == (FloorPlan.objects.count(), FloorPlanAsset.objects.count(), IngestionRun.objects.count())
    plan.refresh_from_db()
    assert plan.review_state == "needs_review"


@pytest.mark.django_db
@pytest.mark.parametrize("busy_scope", ["document", "crop-cart"])
@pytest.mark.parametrize("rotation", [0, 90])
def test_manual_crop_busy_warning_preserves_selection_and_allows_retry(source, client, monkeypatch, busy_scope, rotation):
    from django.urls import reverse
    from platform_api import crop_cart
    from platform_api.ingestion_runtime import WorkAlreadyClaimed

    _, plan, actor = source
    existing = stage_crops(plan, [REGION], actor)["draftIds"]
    counts = (FloorPlan.objects.count(), FloorPlanAsset.objects.count(), AuditEvent.objects.count())
    before = FloorPlan.objects.values().get(pk=plan.pk)
    regions = [{**REGION, "label": '<B wing & "east">'},
               {**REGION, "x": .55, "width": .3, "label": "B wing west"}]
    submitted = {"regions_json": json.dumps(regions), "rotation_degrees": str(rotation)}
    client.force_login(actor)
    url = reverse("admin:platform_api_floorplan_manual_crops", args=[plan.pk])
    real_lock = crop_cart.work_lock

    @contextmanager
    def busy_lock(namespace, identifier):
        if namespace == busy_scope:
            raise WorkAlreadyClaimed(str(identifier))
        with real_lock(namespace, identifier):
            yield

    with monkeypatch.context() as scoped:
        scoped.setattr(crop_cart, "work_lock", busy_lock)
        response = client.post(url, submitted)
    assert response.status_code == 200
    assert b'role="alert"' in response.content
    assert b"Nothing was saved" in response.content
    form = response.context_data["form"]
    assert json.loads(form["regions_json"].value()) == regions
    assert int(form["rotation_degrees"].value()) == rotation
    assert '<B wing & "east">' not in response.content.decode()
    assert list(LayoutCropDraft.objects.values_list("pk", flat=True)) == [LayoutCropDraft.objects.get(pk=existing[0]).pk]
    assert FloorPlan.objects.values().get(pk=plan.pk) == before
    assert counts == (FloorPlan.objects.count(), FloorPlanAsset.objects.count(), AuditEvent.objects.count())
    assert not OutboxMessage.objects.exists()

    response = client.post(url, submitted)
    assert response.status_code == 302 and "#draft-crop-cart" in response.url
    drafts = list(LayoutCropDraft.objects.exclude(pk__in=existing).order_by("created_at"))
    assert [draft.label for draft in drafts] == [region["label"] for region in regions]
    assert all(draft.bounds["rotationDegreesCounterClockwise"] == rotation for draft in drafts)
    assert not OutboxMessage.objects.exists()


@pytest.mark.django_db
def test_cart_limit_is_cumulative(source):
    _, plan, actor = source
    for _ in range(20):
        stage_crops(plan, [REGION], actor)
    with pytest.raises(ValueError, match="at most 20"):
        stage_crops(plan, [REGION], actor)
    assert LayoutCropDraft.objects.count() == 20


@pytest.mark.django_db
def test_add_selected_preserves_others_and_current_layout(source):
    project, plan, actor = source
    one = stage_crops(plan, [REGION], actor)["draftIds"]
    two = stage_crops(plan, [REGION], actor)["draftIds"]
    new_ids = attach_drafts(project, actor, one)
    assert len(new_ids) == 1
    assert LayoutCropDraft.objects.filter(pk=two[0]).exists()
    assert OutboxMessage.objects.count() == 1
    plan.refresh_from_db()
    assert plan.review_state == "needs_review"
    request = RequestFactory().get("/?section=layouts")
    request.user = actor
    dashboard = ProjectAdmin(Project, admin.site).media_dashboard_view(request, str(project.pk))
    assert dashboard.context_data["counts"]["layouts"] == 2
    attach_drafts(project, actor, two)
    dashboard = ProjectAdmin(Project, admin.site).media_dashboard_view(request, str(project.pk))
    assert dashboard.context_data["counts"]["layouts"] == 3


@pytest.mark.django_db
def test_replace_is_explicit_and_audited(source):
    project, plan, actor = source
    ids = stage_crops(plan, [REGION], actor)["draftIds"]
    attach_drafts(project, actor, ids, [plan.pk])
    plan.refresh_from_db()
    assert plan.review_state == "replaced_by_user"
    assert AuditEvent.objects.filter(action="layout.user_replacement").exists()
    assert FloorPlan.objects.filter(pk=plan.pk).exists()


@pytest.mark.django_db
def test_other_owner_cannot_add_or_discard(source):
    project, plan, actor = source
    ids = stage_crops(plan, [REGION], actor)["draftIds"]
    other = get_user_model().objects.create_superuser(username="other-crop-owner", password="test-only")
    with pytest.raises(ValueError):
        attach_drafts(project, other, ids)
    with pytest.raises(ValueError):
        discard_drafts(project, other, ids)
    assert LayoutCropDraft.objects.count() == 1


@pytest.mark.django_db
def test_stale_draft_preserves_cart_and_project(source):
    project, plan, actor = source
    ids = stage_crops(plan, [REGION], actor)["draftIds"]
    FloorPlanAsset.objects.filter(pk=plan.asset_id).update(sha256="0" * 64)
    with pytest.raises(ValueError, match="Source changed"):
        attach_drafts(project, actor, ids)
    assert LayoutCropDraft.objects.count() == 1
    assert FloorPlan.objects.count() == 1
    assert not OutboxMessage.objects.exists()