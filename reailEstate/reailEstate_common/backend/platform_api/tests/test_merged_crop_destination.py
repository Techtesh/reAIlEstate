"""Merged evidence stays with its source; new drafts belong to the canonical project."""

import json

import pytest
from django.contrib import admin
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from django.urls import reverse

from platform_api.admin import FloorPlanAdmin
from platform_api.crop_cart import attach_drafts, crop_destination, stage_crops
from platform_api.models import AuditEvent, FloorPlan, LayoutCropDraft, Organization, OutboxMessage, Project, ProjectMerge
from .test_crop_cart import source, REGION

pytestmark = pytest.mark.django_db


def merged_source(source):
    old, plan, actor = source
    target = Project.objects.create(organization=old.organization, submitted_name="Canonical project")
    merge = ProjectMerge.objects.create(source_project=old, target_project=target, state="applied", reason_code="TEST")
    old.status = "merged"
    old.save(update_fields=["status"])
    return old, plan, actor, target, merge


def test_merged_source_stages_and_attaches_only_under_canonical_project(source):
    old, plan, actor, target, _ = merged_source(source)
    result = stage_crops(plan, [REGION], actor)
    draft = LayoutCropDraft.objects.get()
    assert draft.project_id == target.pk and draft.source_plan_id == plan.pk
    assert result["projectId"] == str(target.pk)
    assert not OutboxMessage.objects.exists() and FloorPlan.objects.count() == 1
    event = AuditEvent.objects.get(action="layout.crop_drafts_saved")
    assert event.entity_id == target.pk and event.metadata["sourceProjectId"] == str(old.pk)
    ids = attach_drafts(target, actor, result["draftIds"])
    attached = FloorPlan.objects.get(pk=ids[0])
    assert attached.project_id == target.pk and attached.document_id == plan.document_id
    assert OutboxMessage.objects.count() == 1
    plan.refresh_from_db()
    old.refresh_from_db()
    assert plan.project_id == old.pk and plan.document.project_id == old.pk
    assert old.status == "merged"


@pytest.mark.parametrize("invalid", ["retired_source", "retired_target", "merged_target", "proposed", "cross_org", "missing"])
def test_invalid_merge_does_not_bypass_lifecycle(source, invalid):
    old, plan, actor, target, merge = merged_source(source)
    if invalid == "retired_source":
        Project.objects.filter(pk=old.pk).update(status="retired")
    elif invalid in {"retired_target", "merged_target"}:
        Project.objects.filter(pk=target.pk).update(status=invalid.split("_")[0])
    elif invalid == "proposed":
        merge.state = "proposed"
        merge.save()
    elif invalid == "missing":
        merge.delete()
    else:
        target.organization = Organization.objects.create(legal_name="Other organization")
        target.save()
    with pytest.raises(ValueError):
        stage_crops(plan, [REGION], actor)
    assert not LayoutCropDraft.objects.exists() and not OutboxMessage.objects.exists()


def test_canonical_project_unchanged(source):
    project, plan, actor = source
    assert crop_destination(project).pk == project.pk
    result = stage_crops(plan, [REGION], actor)
    assert result["projectId"] == str(project.pk)


def test_old_crop_route_preserves_posted_regions_and_redirects_to_canonical_cart(source):
    from .workspace_helpers import workspace_request
    from django.test import Client
    from uuid import uuid4
    old,plan,actor,target,_=merged_source(source)
    view=FloorPlanAdmin(FloorPlan,admin.site)
    path=reverse('admin:platform_api_floorplan_manual_crops',args=[plan.pk])
    get=RequestFactory().get(path);get.user=actor
    response=view.manual_crops_view(get,str(plan.pk))
    assert response.status_code==302 and str(target.pk) in response.url
    post=RequestFactory().post(path,{'regions_json':json.dumps([REGION]),'rotation_degrees':'0'});post.user=actor
    assert view.manual_crops_view(post,str(plan.pk)).status_code==405
    client=Client();client.force_login(actor)
    response=workspace_request(client,target,'/media/crops',{'documentId':str(plan.document_id),'pageNumber':plan.page_number,
        'sourceSha256':plan.document.sha256,'regions':[REGION],'requestId':str(uuid4())},method='post')
    assert response.status_code==202,response.content
    assert LayoutCropDraft.objects.get().project_id==target.pk and not OutboxMessage.objects.exists()


def test_merge_retired_during_image_preparation_leaves_no_drafts(source, monkeypatch):
    from platform_api import crop_cart
    old, plan, actor, target, _ = merged_source(source)
    real_crop = crop_cart.crop_region
    def retire(image, region):
        Project.objects.filter(pk=target.pk).update(status="retired")
        return real_crop(image, region)
    monkeypatch.setattr(crop_cart, "crop_region", retire)
    with pytest.raises(ValueError):
        stage_crops(plan, [REGION], actor)
    assert not LayoutCropDraft.objects.exists()

def test_shared_workspace_evaluates_and_reprocesses_merged_evidence(source, client):
    from uuid import uuid4
    from .workspace_helpers import workspace_request
    old, plan, actor, target, _ = merged_source(source)
    client.force_login(actor)
    response = workspace_request(client, target, '/media/evaluate', {
        'planId': str(plan.pk), 'expectedUpdatedAt': plan.updated_at.isoformat(),
        'confirm': True, 'requestId': str(uuid4()),
    }, method='post')
    assert response.status_code == 202, response.content
    assert OutboxMessage.objects.filter(payload__floorPlanId=str(plan.pk)).count() == 1
    response = workspace_request(client, target, '/media/actions', {
        'action': 'reprocess-all', 'targets': [], 'confirm': True, 'requestId': str(uuid4()),
    }, method='post')
    assert response.status_code == 202, response.content
    assert OutboxMessage.objects.filter(event_type='document.processing.requested', payload__documentId=str(plan.document_id)).count() == 1
    plan.refresh_from_db()
    assert plan.project_id == old.pk
