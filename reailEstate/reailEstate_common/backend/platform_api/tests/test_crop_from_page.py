"""Regression coverage for draft-only source-page cropping (not whole-page layouts)."""

import hashlib
import io
import json
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import pymupdf
import pytest
from PIL import Image
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.http import Http404
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from platform_api import crop_from_page, floor_plan_vision, layout_reanalysis
from platform_api.admin import ProjectAdmin
from platform_api.artifact_storage import storage
from platform_api.crop_cart import attach_drafts, discard_drafts, stage_crops
from platform_api.crop_from_page import stage_page_crops
from platform_api.models import (
    AgentWorkflowRun, AuditEvent, EvidenceDocument, FloorPlan, FloorPlanAsset,
    IngestionRun, LayoutCropDraft, Operation, Organization, OutboxMessage, Project,
    ProjectMerge, ReviewTask,
)

REGION = {"x": .1, "y": .2, "width": .3, "height": .4, "label": "Selected home"}
SECOND = {"x": .6, "y": .2, "width": .3, "height": .4, "label": "Second home"}
PUBLISHED_MODELS = (FloorPlan, FloorPlanAsset, IngestionRun, AgentWorkflowRun,
                    Operation, OutboxMessage, ReviewTask, AuditEvent)


def published_counts():
    return tuple(model.objects.count() for model in PUBLISHED_MODELS)


@pytest.fixture
def page_source(tmp_path, settings, monkeypatch):
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.WORKSPACE_DATA_ROOT = tmp_path
    actor = get_user_model().objects.create_superuser(username="page-cropper", password="test-only")
    org = Organization.objects.create(legal_name="Page crops")
    project = Project.objects.create(organization=org, submitted_name="Missing layouts")
    with pymupdf.open() as pdf:
        for number in range(3):
            page = pdf.new_page(width=400, height=300)
            page.insert_text((40, 40), f"Original page {number + 1}")
            page.draw_rect(pymupdf.Rect(40, 60, 160, 180), color=(0, 0, 1), fill=(0, .5, 1))
        data = pdf.tobytes()
    document = EvidenceDocument.objects.create(
        project=project, original_name="original.pdf", declared_media_type="application/pdf",
        object_key=storage.save(f"sources/{uuid4()}.pdf", ContentFile(data)), byte_length=len(data),
        sha256=hashlib.sha256(data).hexdigest(), scan_status="clean", status="needs_review",
        uploaded_by=actor, retention_until=timezone.now() + timedelta(days=30),
    )
    selection = Mock(return_value={"profileId": "test"})
    monkeypatch.setattr(floor_plan_vision, "resolve_ai_vision_selection", selection)
    return project, document, actor, data, selection


def save(source, page=2, regions=None, rotation=0):
    project, document, actor, *_ = source
    return stage_page_crops(project, document.pk, page, regions or [REGION], actor, document.sha256, rotation)


@pytest.mark.django_db
@pytest.mark.parametrize("category", ["floor-plans", "amenities"])
def test_whole_pages_are_drafts_until_committed(page_source, category, settings):
    from platform_api.crop_cart import commit_saved_draft, stage_source_pages

    project, document, actor, _, selection = page_source
    settings.FLOOR_PLAN_VISION_PROFILES = {"test": {"provider": "ollama", "model": "offline-only"}}
    settings.FLOOR_PLAN_DEFAULT_VISION_PROFILE = "test"
    before = published_counts()
    draft = stage_source_pages(project, document.pk, [2], category, actor)[0]
    assert published_counts() == before
    selection.assert_not_called()
    with pytest.raises(ValueError, match="Commit saved"):
        attach_drafts(project, actor, [draft.pk])
    assert commit_saved_draft(project, actor, draft.pk) == "page"
    assert not LayoutCropDraft.objects.exists()
    assert OutboxMessage.objects.count() == 1
    assert FloorPlanAsset.objects.get().sha256 == draft.sha256


@pytest.mark.django_db
def test_changed_source_page_commit_retains_draft(page_source):
    from platform_api.crop_cart import commit_saved_draft, stage_source_pages

    project, document, actor, *_ = page_source
    draft = stage_source_pages(project, document.pk, [2], "floor-plans", actor)[0]
    document.sha256 = "f" * 64
    document.save()
    before = published_counts()
    with pytest.raises(ValueError):
        commit_saved_draft(project, actor, draft.pk)
    assert published_counts() == before
    assert LayoutCropDraft.objects.filter(pk=draft.pk).exists()


@pytest.mark.django_db
def test_save_writes_only_drafts_from_selected_page(page_source, monkeypatch):
    project, document, actor, original, selection = page_source
    renderer = Mock(wraps=crop_from_page.render_source_pages)
    monkeypatch.setattr(crop_from_page, "render_source_pages", renderer)
    before = published_counts()
    result = save(page_source, rotation=90)
    renderer.assert_called_once()
    assert renderer.call_args.args[1] == [2]
    selection.assert_not_called()
    assert published_counts() == before
    assert result["planIds"] == result["queued"] == []
    draft = LayoutCropDraft.objects.get(pk=result["draftIds"][0])
    assert (draft.project_id, draft.owner_id, draft.document_id, draft.page_number) == (project.pk, actor.pk, document.pk, 2)
    assert draft.source_plan_id is None and draft.source_sha256 == document.sha256
    assert draft.bounds["rotationDegreesCounterClockwise"] == 90
    assert draft.bounds["originalPageBounds"]["unit"] == "normalized"
    assert {key: draft.bounds["originalPageBounds"][key] for key in ("x", "y", "width", "height")} == pytest.approx(
        {"x": .4, "y": .1, "width": .4, "height": .3},
    )
    assert hashlib.sha256(bytes(draft.image)).hexdigest() == draft.sha256
    with Image.open(io.BytesIO(bytes(draft.image))) as crop:
        assert crop.size == (draft.pixel_width, draft.pixel_height)
    with storage.open(document.object_key, "rb") as stream:
        assert stream.read() == original


@pytest.mark.django_db
def test_attach_only_selected_page_crops_no_whole_page_layout(page_source, monkeypatch):
    project, document, actor, *_ = page_source
    selected = save(page_source, regions=[REGION, SECOND])["draftIds"]
    unselected = save(page_source, page=3)["draftIds"]
    renderer = Mock(wraps=crop_from_page.render_source_pages)
    monkeypatch.setattr(crop_from_page, "render_source_pages", renderer)
    new_ids = attach_drafts(project, actor, selected)
    renderer.assert_called_once()
    assert renderer.call_args.args[1] == [2]
    assert set(FloorPlanAsset.objects.values_list("page_number", flat=True)) == {2}
    source_asset = FloorPlanAsset.objects.get(asset_type="page_render")
    assert not source_asset.floor_plans.exists()
    assert source_asset.source_bounds["sourceSha256"] == document.sha256
    assert FloorPlan.objects.count() == 2
    assert FloorPlan.objects.filter(pk__in=new_ids, asset__asset_type="unit_crop", plan_scope="flat").count() == 2
    assert FloorPlanAsset.objects.filter(asset_type="unit_crop", source_bounds__sourceAssetId=str(source_asset.pk)).count() == 2
    assert str(LayoutCropDraft.objects.get().pk) == unselected[0]
    assert OutboxMessage.objects.count() == 2
    assert set(OutboxMessage.objects.values_list("event_type", flat=True)) == {"floor_plan.crop_evaluation.requested"}
    assert {row.payload["floorPlanId"] for row in OutboxMessage.objects.all()} == set(new_ids)
    # Another explicit attachment from the same page reuses its source-only asset.
    attach_drafts(project, actor, save(page_source)["draftIds"])
    assert FloorPlanAsset.objects.filter(asset_type="page_render").count() == 1
    assert FloorPlan.objects.filter(asset__asset_type="page_render").count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("change", ["metadata", "bytes", "scan"])
def test_stale_original_cannot_save_or_attach(page_source, change):
    project, document, actor, original, _ = page_source
    ids = save(page_source)["draftIds"]
    if change == "metadata":
        EvidenceDocument.objects.filter(pk=document.pk).update(sha256="0" * 64)
    elif change == "scan":
        EvidenceDocument.objects.filter(pk=document.pk).update(scan_status="infected")
    else:
        with storage.open(document.object_key, "wb") as stream:
            stream.write(original + b"tampered")
    before = published_counts()
    with pytest.raises(ValueError):
        save(page_source)
    with pytest.raises(ValueError):
        attach_drafts(project, actor, ids)
    assert LayoutCropDraft.objects.count() == 1
    assert published_counts() == before


@pytest.mark.django_db
@pytest.mark.parametrize("checksum", [None, "", "0" * 64])
def test_preview_original_checksum_is_mandatory(page_source, checksum):
    project, document, actor, *_ = page_source
    with pytest.raises(ValueError, match="Source changed"):
        stage_page_crops(project, document.pk, 2, [REGION], actor, checksum)
    assert not LayoutCropDraft.objects.exists()
    assert published_counts() == (0,) * len(PUBLISHED_MODELS)


@pytest.mark.django_db
@pytest.mark.parametrize("page,regions,rotation", [
    (0, [REGION], 0), (4, [REGION], 0), (True, [REGION], 0),
    (2, [], 0), (2, [REGION], 45), (2, [REGION], True),
    (2, [{"x": 0, "y": 0, "width": 1, "height": 1}], 0),
    (2, [REGION, REGION], 0),
])
def test_invalid_or_whole_page_selection_does_not_write(page_source, page, regions, rotation):
    project, document, actor, *_ = page_source
    with pytest.raises(ValueError):
        stage_page_crops(project, document.pk, page, regions, actor, document.sha256, rotation)
    assert not LayoutCropDraft.objects.exists()
    assert published_counts() == (0,) * len(PUBLISHED_MODELS)


@pytest.mark.django_db
def test_batch_save_failure_is_atomic(page_source, monkeypatch):
    real_crop = crop_from_page.crop_region
    attempts = 0

    def fail_second(image, region):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise ValueError("Second crop failed")
        return real_crop(image, region)

    monkeypatch.setattr(crop_from_page, "crop_region", fail_second)
    with pytest.raises(ValueError, match="Second crop failed"):
        save(page_source, regions=[REGION, SECOND])
    assert not LayoutCropDraft.objects.exists()
    assert published_counts() == (0,) * len(PUBLISHED_MODELS)


@pytest.mark.django_db
def test_attachment_failure_preserves_all_drafts_and_cleans_assets(page_source, monkeypatch):
    project, _, actor, *_ = page_source
    ids = save(page_source, regions=[REGION, SECOND])["draftIds"]
    queued = layout_reanalysis.queue_layout_evaluation
    attempts = 0
    keys = []
    real_save = storage.save

    def record_save(*args, **kwargs):
        key = real_save(*args, **kwargs)
        keys.append(key)
        return key

    def fail_second(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise ValueError("Queue unavailable")
        return queued(*args, **kwargs)

    monkeypatch.setattr(storage, "save", record_save)
    monkeypatch.setattr(layout_reanalysis, "queue_layout_evaluation", fail_second)
    with pytest.raises(ValueError, match="Queue unavailable"):
        attach_drafts(project, actor, ids)
    assert LayoutCropDraft.objects.count() == 2
    assert published_counts() == (0,) * len(PUBLISHED_MODELS)
    assert keys and all(not storage.exists(key) for key in keys)


@pytest.mark.django_db
def test_owner_and_project_scoping(page_source):
    project, document, actor, *_ = page_source
    ids = save(page_source)["draftIds"]
    other = get_user_model().objects.create_superuser(username="other-page-cropper", password="test-only")
    foreign = Project.objects.create(organization=project.organization, submitted_name="Unrelated")
    for method in (attach_drafts, discard_drafts):
        with pytest.raises(ValueError):
            method(project, other, ids)
        with pytest.raises(ValueError):
            method(foreign, actor, ids)
    with pytest.raises(ValueError, match="project family"):
        stage_page_crops(foreign, document.pk, 1, [REGION], actor, document.sha256)
    from django.test import Client
    from .workspace_helpers import workspace_request
    client=Client();client.force_login(other)
    assert workspace_request(client,project,f'/media/drafts/{ids[0]}').status_code==404
    assert LayoutCropDraft.objects.count() == 1
    assert not FloorPlan.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("flag", ["inactive", "not_staff", "no_permission"])
def test_active_staff_with_change_floorplan_required(page_source, flag):
    project, document, actor, *_ = page_source
    if flag == "inactive":
        actor.is_active = False
    elif flag == "not_staff":
        actor.is_staff = False
    else:
        actor.is_superuser = False
    with pytest.raises(PermissionDenied):
        stage_page_crops(project, document.pk, 2, [REGION], actor, document.sha256)
    assert not LayoutCropDraft.objects.exists()


def legacy_plan(project, document):
    data = crop_from_page.render_source_pages(document, [1])[0][1]
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
    run = IngestionRun.objects.create(project=project, document=document)
    workflow = AgentWorkflowRun.objects.create(project=project, document=document, ingestion_run=run)
    asset = FloorPlanAsset.objects.create(
        project=project, document=document, workflow_run=workflow, page_number=1, asset_type="page_render",
        object_key=storage.save(f"sources/{uuid4()}.png", ContentFile(data)), byte_length=len(data),
        sha256=hashlib.sha256(data).hexdigest(), pixel_width=width, pixel_height=height, detector_version="legacy",
    )
    return FloorPlan.objects.create(project=project, document=document, workflow_run=workflow,
                                   asset=asset, page_number=1, plan_scope="flat", plan_name="Legacy", extractor_version="legacy")


@pytest.mark.django_db
def test_legacy_and_source_page_drafts_attach_together_and_reuse_original(page_source):
    project, document, actor, *_ = page_source
    legacy = legacy_plan(project, document)
    ids = stage_crops(legacy, [REGION], actor)["draftIds"] + save(page_source, page=1)["draftIds"]
    new_ids = attach_drafts(project, actor, ids)
    assert len(new_ids) == 2 and not LayoutCropDraft.objects.exists()
    assert FloorPlanAsset.objects.filter(asset_type="page_render").count() == 1
    legacy.refresh_from_db()
    assert legacy.review_state == "needs_review"
    assert FloorPlan.objects.count() == 3
    assert OutboxMessage.objects.count() == 2


@pytest.mark.django_db
def test_source_page_replacement_requires_explicit_targets(page_source):
    project, document, actor, *_ = page_source
    old = legacy_plan(project, document)
    first = attach_drafts(project, actor, save(page_source)["draftIds"])
    old.refresh_from_db()
    assert old.review_state == "needs_review"
    replacements = attach_drafts(project, actor, save(page_source)["draftIds"], [old.pk])
    old.refresh_from_db()
    assert old.review_state == "replaced_by_user"
    assert old.raw_extraction["userReplacement"]["replacementPlanIds"] == replacements
    assert FloorPlan.objects.get(pk=first[0]).review_state == "manual_crop_needs_evaluation"
    assert AuditEvent.objects.filter(action="layout.user_replacement").exists()


@pytest.mark.django_db
def test_cumulative_limit_includes_legacy_drafts_from_other_projects(page_source):
    project, _, actor, data, _ = page_source
    other = Project.objects.create(organization=project.organization, submitted_name="Other project")
    other_document = EvidenceDocument.objects.create(
        project=other, original_name="other.pdf", declared_media_type="application/pdf",
        object_key=storage.save(f"sources/{uuid4()}.pdf", ContentFile(data)), byte_length=len(data),
        sha256=hashlib.sha256(data).hexdigest(), scan_status="clean", uploaded_by=actor,
        retention_until=timezone.now() + timedelta(days=30),
    )
    legacy = legacy_plan(other, other_document)
    template = LayoutCropDraft.objects.get(pk=stage_crops(legacy, [REGION], actor)["draftIds"][0])
    # Populate unrelated cart slots without rendering nineteen identical fixtures.
    for _ in range(19):
        LayoutCropDraft.objects.create(
            owner=actor, project=other, source_plan=legacy, source_sha256=template.source_sha256,
            label=template.label, image=template.image, sha256=template.sha256,
            pixel_width=template.pixel_width, pixel_height=template.pixel_height, bounds=template.bounds,
        )
    with pytest.raises(ValueError, match="at most 20"):
        save(page_source)
    assert LayoutCropDraft.objects.filter(owner=actor).count() == 20


@pytest.mark.django_db
def test_family_source_stays_with_original_document(page_source):
    project, document, actor, *_ = page_source
    existing = legacy_plan(project, document)
    target = Project.objects.create(organization=project.organization, submitted_name="Canonical project")
    ProjectMerge.objects.create(source_project=project, target_project=target, state="applied", reason_code="duplicate", merged_by=actor)
    Project.objects.filter(pk=project.pk).update(status="merged")
    ids = stage_page_crops(target, document.pk, 1, [REGION], actor, document.sha256)["draftIds"]
    plan = FloorPlan.objects.get(pk=attach_drafts(target, actor, ids)[0])
    assert plan.project_id == target.pk and plan.document_id == document.pk
    assert plan.asset.source_bounds["sourceAssetId"] == str(existing.asset_id)
    assert FloorPlanAsset.objects.filter(asset_type="page_render").count() == 1
    document.refresh_from_db()
    assert document.project_id == project.pk


@pytest.mark.django_db
def test_model_constraint_rejects_neither_or_both_sources(page_source):
    project, document, *_ = page_source
    draft_id = save(page_source)["draftIds"][0]
    legacy = legacy_plan(project, document)
    for values in ({"document": None}, {"page_number": None}, {"page_number": 0}, {"source_plan": legacy}):
        with pytest.raises(IntegrityError), transaction.atomic():
            LayoutCropDraft.objects.filter(pk=draft_id).update(**values)


@pytest.mark.django_db
def test_add_layout_view_uses_cropper_and_old_page_post_cannot_queue(page_source, client):
    from .workspace_helpers import workspace_request
    project,document,actor,*_=page_source
    client.force_login(actor)
    url=reverse('admin:platform_api_project_add_source_pages',args=[project.pk])
    assert client.get(url,{'category':'layouts','document':document.pk,'page_number':2}).status_code==302
    assert client.post(url,{'category':'layouts','document':document.pk,'pages':[1,2]}).status_code==405
    assert not LayoutCropDraft.objects.exists() and not OutboxMessage.objects.exists()
    response=workspace_request(client,project,'/media/crops',{'documentId':str(document.pk),'pageNumber':2,'sourceSha256':document.sha256,
        'regions':[REGION],'rotationDegrees':0,'requestId':str(uuid4())},method='post')
    assert response.status_code==202,response.content
    assert LayoutCropDraft.objects.count()==1
    assert published_counts()[:-1]==(0,)*(len(PUBLISHED_MODELS)-1)
    assert AuditEvent.objects.count()==1


@pytest.mark.django_db
def test_preview_rejects_changed_or_missing_checksum(page_source, client):
    project, document, actor, *_ = page_source
    client.force_login(actor)
    url = reverse("admin:platform_api_project_source_preview", args=[project.pk, document.pk, 2])
    assert client.get(url, {"category": "layouts"}).status_code == 404
    assert client.get(url, {"category": "layouts", "source_sha256": "0" * 64}).status_code == 404
    assert client.get(url, {"category": "layouts", "source_sha256": document.sha256}).status_code == 200