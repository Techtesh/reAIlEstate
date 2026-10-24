"""Fresh attempts are enqueue-only, signed, current-profile and history-safe."""

from copy import deepcopy
from unittest.mock import patch

import pytest
from .legacy_admin_urls import legacy_admin_adapters  # noqa: F401 - opt-in adapter regression harness
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone

from platform_api.floor_plan_layout_jobs import queue_floor_plan_layouts
from platform_api.layout_reanalysis import queue_layout_evaluation
from platform_api.models import FloorPlan, Operation, OutboxMessage, Project, QueueWorker
from platform_api.reanalysis import evaluation_retry_warning, fresh_evaluation_plans, queue_fresh_project_evaluations
from .test_floor_plan_layout_jobs import source


pytestmark = pytest.mark.django_db(transaction=True)


def queue(plan, actor, **kwargs):
    if plan.plan_scope == "floor":
        job = queue_floor_plan_layouts(plan, actor, **kwargs)
        return job, Operation.objects.get(pk=job.payload["operationId"])
    return queue_layout_evaluation(plan, actor, **kwargs)


def fail(job, operation):
    operation.state = "failed"
    operation.error_code = "InvalidProviderResponse"
    operation.save()
    job.dead_lettered_at = timezone.now()
    job.last_error_code = operation.error_code
    job.attempts = 3
    job.save()


@pytest.mark.parametrize("scope", ["flat", "floor"])
@pytest.mark.parametrize("state", ["failed", "completed"])
def test_fresh_uses_current_ai_profile_preserves_attempt_and_deduplicates(source, settings, scope, state):
    plan, actor = source
    plan.plan_scope = scope
    plan.dimensions = [{"room": "living", "length": 7, "width": 3.87}]
    plan.save()
    old, operation = queue(plan, actor)
    if state == "failed":
        fail(old, operation)
    else:
        operation.state = state
        operation.save()
        old.published_at = timezone.now()
        old.save()
    original_job = deepcopy(OutboxMessage.objects.values().get(pk=old.pk))
    original_operation = deepcopy(Operation.objects.values().get(pk=operation.pk))
    settings.FLOOR_PLAN_DEFAULT_VISION_PROFILE = "other"
    fresh, pending = queue(plan, actor, retry=True, fresh=True)
    assert fresh.pk != old.pk and pending.pk != operation.pk
    assert pending.state == "pending" and not pending.error_code
    assert fresh.payload["visionSelection"]["profileId"] is None
    assert fresh.payload["visionSelection"]["provider"] == "automatic"
    assert OutboxMessage.objects.values().get(pk=old.pk) == original_job
    assert Operation.objects.values().get(pk=operation.pk) == original_operation
    assert queue(plan, actor, retry=True, fresh=True)[0].pk == fresh.pk
    plan.refresh_from_db()
    assert plan.dimensions == [{"room": "living", "length": 7, "width": 3.87}]


@pytest.mark.parametrize("scope", ["flat", "floor"])
@pytest.mark.parametrize("state", ["pending", "running", "cancelled", "failed"])
def test_fresh_never_forks_pending_running_cancelled_or_backoff(source, settings, scope, state):
    plan, actor = source
    plan.plan_scope = scope
    plan.save()
    job, operation = queue(plan, actor)
    operation.state = state
    operation.save()
    if state == "failed":
        job.available_after = timezone.now()
        job.attempts = 1
        job.save()
    settings.FLOOR_PLAN_DEFAULT_VISION_PROFILE = "other"
    original = deepcopy(job.payload)
    retained, saved = queue(plan, actor, retry=True, fresh=True)
    assert retained.pk == job.pk and saved.state == state
    assert retained.payload == original and OutboxMessage.objects.count() == 1


@pytest.mark.parametrize("scope", ["flat", "floor"])
def test_project_fresh_handles_deleted_dlq_without_touching_successful_or_other_projects(source, settings, scope):
    plan, actor = source
    plan.plan_scope = scope
    plan.save()
    old, operation = queue(plan, actor)
    fail(old, operation)
    old.delete()
    completed = FloorPlan.objects.create(project=plan.project, document=plan.document, asset=plan.asset,
        workflow_run=plan.workflow_run, page_number=1, plan_scope=scope, plan_name="Already fixed", extractor_version="test")
    previous, failed = queue(completed, actor)
    fail(previous, failed)
    newer, finished = queue(completed, actor, fresh=True)
    finished.state = "completed"
    finished.save()
    other_project = Project.objects.create(organization=plan.project.organization, submitted_name="Other project")
    other_plan = FloorPlan.objects.create(project=other_project, document=plan.document, asset=plan.asset,
        workflow_run=plan.workflow_run, page_number=1, plan_scope=scope, plan_name="Other failed", extractor_version="test")
    Operation.objects.create(owner=actor, kind=operation.kind, state="failed", result={
        "floorPlanId": str(other_plan.pk), "assetId": str(plan.asset_id), "assetSha256": plan.asset.sha256})
    settings.FLOOR_PLAN_DEFAULT_VISION_PROFILE = "other"
    assert fresh_evaluation_plans(plan.project) == [plan]
    jobs = queue_fresh_project_evaluations(plan.project, actor)
    assert len(jobs) == 1 and jobs[0] != str(newer.pk)
    assert OutboxMessage.objects.get(pk=jobs[0]).payload["visionSelection"]["profileId"] is None
    operation.refresh_from_db()
    assert operation.state == "failed" and operation.error_code == "InvalidProviderResponse"
    assert queue_fresh_project_evaluations(plan.project, actor) == []
    assert Operation.objects.filter(result__floorPlanId=str(other_plan.pk)).count() == 1


@pytest.mark.parametrize("action", ["run_layout_evaluation", "queue_floor_layouts", "run_project_evaluations"])
def test_fresh_confirmation_is_bound_to_choice_and_dispatches_current_profile(source, settings, client, action):
    from .workspace_helpers import workspace_request,media_action
    from uuid import uuid4
    plan,actor=source
    if action!='queue_floor_layouts':plan.plan_scope='flat';plan.save()
    old,operation=queue(plan,actor);fail(old,operation)
    settings.FLOOR_PLAN_DEFAULT_VISION_PROFILE='other'
    client.force_login(actor)
    # The JSON editor explicitly confirms costs and selects fresh vs saved settings.
    if action=='queue_floor_layouts':
        response=media_action(client,plan,'segment',fresh=True)
    else:
        body={'planId':str(plan.pk),'expectedUpdatedAt':plan.updated_at.isoformat(),'requestId':str(uuid4()),'confirm':False,'fresh':True}
        assert workspace_request(client,plan.project,'/media/evaluate',body,method='post').status_code==400
        assert OutboxMessage.objects.count()==1
        body['confirm']=True
        response=workspace_request(client,plan.project,'/media/evaluate',body,method='post')
    assert response.status_code==202,response.content
    assert OutboxMessage.objects.count()==2
    fresh=OutboxMessage.objects.exclude(pk=old.pk).get()
    assert fresh.payload['visionSelection']['profileId'] is None
    old.refresh_from_db();operation.refresh_from_db()
    assert old.attempts==3 and old.dead_lettered_at and operation.state=='failed'


def test_fresh_no_js_form_has_signed_choice_and_confirmation_expires(source, client):
    plan,actor=source
    old,operation=queue(plan,actor);fail(old,operation)
    client.force_login(actor)
    submitted=QueryDict('action=queue_floor_layouts',mutable=True);submitted['floor_plan_id']=str(plan.pk)
    # The retired no-JS form cannot enqueue anything; the existing signed domain
    # confirmation remains bound to actor, choice and its five-minute lifetime.
    assert client.post(reverse('admin:platform_api_project_media_dashboard',args=[plan.project_id]),submitted).status_code==405
    warning=evaluation_retry_warning(plan.project,actor,submitted)
    submitted['fresh_evaluation']='true';submitted['retry_confirmation']=warning['confirmationToken']
    assert evaluation_retry_warning(plan.project,actor,submitted) is not None
    submitted['retry_confirmation']=warning['freshConfirmationToken']
    assert evaluation_retry_warning(plan.project,actor,submitted) is None
    import time
    with patch('django.core.signing.time.time',return_value=time.time()+301):assert evaluation_retry_warning(plan.project,actor,submitted) is not None
    assert OutboxMessage.objects.count()==1


@pytest.mark.parametrize("scope", ["flat", "floor"])
@pytest.mark.parametrize("invalid", ["profile", "source"])
def test_fresh_keeps_profile_and_clean_source_admission_checks(source, settings, scope, invalid):
    plan, actor = source
    plan.plan_scope = scope
    plan.save()
    old, operation = queue(plan, actor)
    fail(old, operation)
    if invalid == "profile":
        settings.FLOOR_PLAN_VISION_PROFILES = {}
        settings.FLOOR_PLAN_DEFAULT_VISION_PROFILE = "missing"
    else:
        plan.document.scan_status = "quarantined"
        plan.document.save()
    with pytest.raises(ValueError):
        queue(plan, actor, fresh=True)
    old.refresh_from_db()
    operation.refresh_from_db()
    assert OutboxMessage.objects.count() == 1 and old.dead_lettered_at and operation.state == "failed"


def test_unrelated_document_warning_does_not_offer_fresh_layout_evaluation(source):
    plan, actor = source
    Operation.objects.create(owner=actor, kind="document_processing", state="failed", error_code="PROVIDER_UNAVAILABLE",
                             result={"documentId": str(plan.document_id)})
    warning = evaluation_retry_warning(plan.project, actor, QueryDict("action=run_project_evaluations"))
    assert warning and "freshConfirmationToken" not in warning
