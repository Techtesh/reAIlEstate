"""Integration contracts for media controls; no live models or destructive actions."""

from unittest.mock import Mock

import pytest
from django.contrib import admin
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from django.urls import reverse

from platform_api.admin import FloorPlanAdmin, ProjectAdmin
from platform_api.models import Project, LayoutCropDraft, OutboxMessage
from .test_crop_cart import source, REGION

pytestmark = pytest.mark.django_db


def request(actor, method="get", data=None):
    factory = RequestFactory()
    req = factory.post("/?section=layouts", data or {}) if method == "post" else factory.get("/?section=layouts")
    req.user = actor
    req.session = {}
    req._messages = FallbackStorage(req)
    return req


def test_dashboard_shows_crop_previews_not_full_floor_sources(source):
    from platform_api.crop_cart import stage_crops,attach_drafts
    from .workspace_helpers import workspace_request,frontend_source
    from django.test import Client
    project,plan,actor=source
    plan.plan_scope='floor';plan.save()
    ids=stage_crops(plan,[REGION],actor)['draftIds'];attached=attach_drafts(project,actor,ids)
    client=Client();client.force_login(actor)
    response=workspace_request(client,project,'/media');assert response.status_code==200
    layouts=[item for item in response.json()['items'] if item['category']=='layouts']
    assert len(layouts)==1 and layouts[0]['planId']==attached[0]
    assert str(plan.asset_id) not in layouts[0]['imageUrl'] and layouts[0]['sourceKind']=='unit_crop'
    assert layouts[0]['canCrop'] and layouts[0]['canEvaluate']
    ui=frontend_source();assert 'Crop drafts' in ui and 'Replace selected' in ui and 'Evaluation' in ui


def test_dashboard_extract_action_only_queues_and_deduplicates(source, monkeypatch):
    from .workspace_helpers import workspace_request
    from django.test import Client
    from uuid import uuid4
    project,plan,actor=source
    worker=Mock(side_effect=AssertionError('Click must not execute models or drain queue'))
    monkeypatch.setattr('platform_api.workers.process_pending_outbox',worker)
    client=Client();client.force_login(actor)
    body={'planId':str(plan.pk),'expectedUpdatedAt':plan.updated_at.isoformat(),'confirm':True,'requestId':str(uuid4())}
    for _ in range(3):
        response=workspace_request(client,project,'/media/evaluate',body,method='post')
        assert response.status_code==202,response.content
    assert OutboxMessage.objects.filter(payload__floorPlanId=str(plan.pk)).count()==1
    worker.assert_not_called()


def test_global_cart_only_groups_current_owner_drafts(source):
    from platform_api.crop_cart import stage_crops
    from django.test import Client,override_settings
    project,plan,actor=source
    stage_crops(plan,[REGION],actor)
    client=Client();client.force_login(actor)
    with override_settings(ROOT_URLCONF='config.urls'):
        response=client.get('/api/v1/admin/project-workspace/builder/crop-cart')
    assert response.status_code==200
    assert response.json()==[{'projectId':str(project.pk),'organizationId':str(project.organization_id),'name':project.submitted_name,'count':1}]
    assert LayoutCropDraft.objects.count()==1


def test_dashboard_cart_adds_selected_draft_through_admin_route(source, client):
    from platform_api.crop_cart import stage_crops
    from .workspace_helpers import workspace_request
    from uuid import uuid4
    project,plan,actor=source
    draft_id=stage_crops(plan,[REGION],actor)['draftIds'][0]
    client.force_login(actor)
    assert client.post(reverse('admin:platform_api_project_crop_cart',args=[project.pk]),{'draft_action':'add','draft_ids':[draft_id]}).status_code==405
    response=workspace_request(client,project,'/media/drafts',{'action':'attach','draftIds':[draft_id],'confirm':True,'requestId':str(uuid4())},method='post')
    assert response.status_code==202,response.content
    assert not LayoutCropDraft.objects.filter(pk=draft_id).exists() and len(response.json()['planIds'])==1


def test_dashboard_cart_explains_unselected_and_inapplicable_actions(source, client):
    from platform_api.crop_cart import stage_crops
    from .workspace_helpers import workspace_request,frontend_source
    from uuid import uuid4
    project,plan,actor=source
    draft_id=stage_crops(plan,[REGION],actor)['draftIds'][0]
    client.force_login(actor)
    response=workspace_request(client,project,'/media/drafts',{'action':'attach','draftIds':[],'confirm':True,'requestId':str(uuid4())},method='post')
    assert response.status_code==400
    assert LayoutCropDraft.objects.filter(pk=draft_id).exists() and not OutboxMessage.objects.exists()
    assert 'selectedDrafts.length' in frontend_source() and 'Categories are retained' in frontend_source()


def test_full_project_health_annotation_can_be_fetched(source):
    project, plan, actor = source
    plan.review_state = "needs_boundary_review"
    plan.save()
    # Fetch whole model, not only counts: catches Coalesce FK/UUID output-field bugs.
    obj = ProjectAdmin(Project, admin.site).get_object(request(actor), str(project.pk))
    assert obj.boundary_review_count == 1
    assert obj.layout_health_project_id == project.pk