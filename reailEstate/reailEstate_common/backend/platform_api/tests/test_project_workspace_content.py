"""Shared editors retain tenant, review, replay and legacy-draft boundaries."""
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4
import pytest
from django.contrib.auth.models import Permission
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from platform_api.external_sources import GoogleProjectMaps
from platform_api.models import AuditEvent, LayoutCropDraft, OrganizationMembership, OutboxMessage, Project, ReviewTask
from .test_crop_from_page import page_source, REGION
from .test_layout_crop_update import layout, edit_args
from .workspace_helpers import workspace_request

pytestmark = pytest.mark.django_db


@pytest.fixture(params=['builder', 'admin'])
def content_workspace(page_source, settings, request):
    project, document, actor, *_ = page_source
    project.maha_rera_id = 'P51800000001'; project.save()
    settings.FLOOR_PLAN_VISION_PROFILES = {'test': {'provider': 'ollama', 'model': 'offline-only'}}
    settings.FLOOR_PLAN_DEFAULT_VISION_PROFILE = 'test'
    settings.ROOT_URLCONF = 'config.urls'
    settings.ALLOWED_HOSTS = ['testserver']; settings.SECURE_SSL_REDIRECT = False
    actor.is_superuser = False
    actor.is_staff = request.param == 'admin'; actor.save()
    if actor.is_staff:
        actor.user_permissions.add(*Permission.objects.filter(content_type__app_label='platform_api',
            codename__in=['change_project','change_floorplan']))
    else:
        OrganizationMembership.objects.create(user=actor,organization=project.organization,role='editor')
    client = APIClient(); client.force_login(actor)
    prefix = '/api/v1/admin/project-workspace' if actor.is_staff else '/api/v1'
    return client,project,document,actor,f'{prefix}/builder/projects/{project.pk}'


def submit(project, **fields):
    return {'action':'amenities','text':'gym and swimming pool','expectedUpdatedAt':project.updated_at.isoformat(),
        'confirm':True,'requestId':str(uuid4()),**fields}


def test_common_amenities_replay_conflict_and_review(content_workspace):
    client,project,_,actor,root = content_workspace
    project.status='published';project.save()
    body=submit(project)
    response=client.post(root+'/content',body,format='json')
    assert response.status_code==201,response.content
    assert client.post(root+'/content',body,format='json').json()==response.json()
    assert client.post(root+'/content',{**body,'text':'clubhouse'},format='json').status_code==409
    assert AuditEvent.objects.filter(action='project.amenities_added_manually').count()==1
    project.refresh_from_db();assert project.status=='needs_review'
    assert project.project_amenities.count()==2
    assert all(item['state']=='unreviewed' for item in client.get(root+'/content').json()['amenities'])
    assert client.post(root+'/content',submit(project,confirm=False),format='json').status_code==400


@pytest.mark.parametrize('restriction',['stale','retired','revoked'])
def test_common_sources_check_access_before_provider(content_workspace,monkeypatch,restriction):
    client,project,_,actor,root=content_workspace
    provider=Mock(side_effect=AssertionError('Unauthorized provider call'))
    monkeypatch.setattr(GoogleProjectMaps,'search',provider)
    body=submit(project,action='search',query='Some project')
    if restriction=='stale':project.save()
    elif restriction=='retired':project.status='retired';project.save()
    elif actor.is_staff:actor.user_permissions.clear()
    else:OrganizationMembership.objects.filter(user=actor).update(status='revoked')
    assert client.post(root+'/content',body,format='json').status_code==({'revoked':403 if actor.is_staff else 404}.get(restriction,409))
    provider.assert_not_called()


def test_common_source_lookup_is_explicit_and_submissions_are_private(content_workspace,monkeypatch):
    client,project,_,_,root=content_workspace
    place={'placeId':'verified-id','name':'Project','address':'Mumbai','location':{'latitude':19.1,'longitude':72.9}}
    search=Mock(return_value=[place]);details=Mock(return_value=place)
    monkeypatch.setattr(GoogleProjectMaps,'search',search);monkeypatch.setattr(GoogleProjectMaps,'details',details)
    assert client.get(root+'/content').status_code==200
    search.assert_not_called();details.assert_not_called()
    body=submit(project,action='search',query='Project Mumbai');body.pop('text')
    assert client.post(root+'/content',body,format='json').json()['places']==[place]
    body=submit(project,action='submit',placeId='verified-id');body.pop('text')
    assert client.post(root+'/content',body,format='json').status_code==201
    details.assert_called_once_with('verified-id')
    task=ReviewTask.objects.get(task_type='external_sources')
    assert task.resolution['googlePlaceId']=='verified-id' and task.resolution['reviewState']=='unreviewed'
    assert 'address' not in task.resolution and 'name' not in task.resolution
    project.refresh_from_db();assert project.latitude is None and project.status!='published'


@pytest.mark.parametrize('invalid',['future','host','registration','shape'])
def test_common_registry_validation_is_atomic(content_workspace,invalid):
    client,project,_,_,root=content_workspace
    registry={'registration_number':project.maha_rera_id,'source_url':'https://maharera.maharashtra.gov.in/project/123',
        'checked_on':timezone.localdate().isoformat(),'evidence':'Registry project record'}
    if invalid=='future':registry['checked_on']=(timezone.localdate()+timedelta(days=1)).isoformat()
    elif invalid=='host':registry['source_url']='https://evil.example/project'
    elif invalid=='registration':registry['registration_number']='P51800000002'
    else:registry=[]
    response=client.post(root+'/content',submit(project,action='submit',registry=registry),format='json')
    assert response.status_code==400,response.content
    assert not ReviewTask.objects.filter(task_type='external_sources').exists()


def test_saved_admin_page_draft_uses_common_cart(content_workspace):
    from platform_api.crop_cart import stage_source_pages
    client,project,document,actor,root=content_workspace
    drafts=stage_source_pages(project,document.pk,[1],'amenities',actor)
    response=client.post(root+'/media/drafts',{'action':'attach','draftIds':[str(d.pk) for d in drafts],
        'confirm':True,'requestId':str(uuid4()),'visionProfileId':'test'},format='json')
    assert response.status_code==202,response.content
    assert not LayoutCropDraft.objects.exists()
    assert project.floor_plan_assets.get().source_bounds['category']=='amenities'
    assert OutboxMessage.objects.count()==1


@pytest.mark.django_db(transaction=True)
def test_saved_legacy_crop_update_uses_common_commit(layout,client):
    from platform_api.crop_cart import stage_crop_update
    plan,actor,_=layout
    args=edit_args(plan,actor);args['rotation_degrees']=5
    draft=stage_crop_update(plan.pk,actor,**args)
    client.force_login(actor)
    response=workspace_request(client,plan.project,'/media/drafts',{'action':'attach','draftIds':[str(draft.pk)],
        'confirm':True,'requestId':str(uuid4())},method='post')
    assert response.status_code==202,response.content
    old_id=plan.pk;plan.refresh_from_db()
    assert plan.pk==old_id and plan.display_name=='Reviewer alias'
    assert not LayoutCropDraft.objects.exists() and OutboxMessage.objects.count()==1
    assert plan.asset.source_bounds['rotationDegreesClockwise']==5


def test_common_staff_fine_crop_without_builder_membership(layout,client):
    plan,actor,_=layout
    actor.is_superuser=False;actor.save()
    actor.user_permissions.add(*Permission.objects.filter(content_type__app_label='platform_api',codename__in=['change_project','change_floorplan']))
    client.force_login(actor)
    response=workspace_request(client,plan.project,'/media/crops',{'documentId':str(plan.document_id),
        'pageNumber':1,'sourceSha256':plan.document.sha256,'category':'layouts','sourceMode':'current','rotationDegrees':5,
        'targetPlanId':str(plan.pk),'expectedUpdatedAt':plan.updated_at.isoformat(),'regions':[{**REGION,'label':'Fine crop'}],
        'requestId':str(uuid4())},method='post')
    assert response.status_code==202,response.content
    committed=workspace_request(client,plan.project,'/media/drafts',{'action':'attach','draftIds':response.json()['draftIds'],
        'confirm':True,'requestId':str(uuid4())},method='post')
    assert committed.status_code==202,committed.content
    plan.refresh_from_db();assert plan.asset.source_bounds['rotationDegreesClockwise']==355
    assert not OrganizationMembership.objects.filter(user=actor).exists()
