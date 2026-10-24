"""Queue status must explain why dimensions are empty. No live provider calls."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from .legacy_admin_urls import legacy_admin_adapters  # noqa: F401 - opt-in adapter regression harness
from django.contrib import admin
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from platform_api.admin import ProjectAdmin
from platform_api.layout_evaluation_status import layout_evaluation_states, queue_single_layout, run_single_layout
from platform_api.models import FloorPlan, Operation, OutboxMessage, Project
from .test_floor_plan_layout_jobs import source

pytestmark = pytest.mark.django_db


def crop_job(plan, actor, state="pending"):
    plan.plan_scope = "flat"
    plan.review_state = "manual_crop_needs_evaluation"
    plan.save()
    operation = Operation.objects.create(owner=actor, kind="floor_plan_crop_evaluation", state=state)
    job = OutboxMessage.objects.create(event_type="floor_plan.crop_evaluation.requested", correlation_id=uuid4(),
        payload={"floorPlanId": str(plan.pk), "assetId": str(plan.asset_id), "assetSha256": plan.asset.sha256,
                 "operationId": str(operation.pk)})
    return job, operation


@pytest.mark.parametrize("state,expected", [("pending", "queued"), ("running", "running"),
    ("failed", "failed"), ("completed", "completed"), ("cancelled", "cancelled")])
def test_empty_dimensions_distinguish_operation_states(source, state, expected):
    plan, actor = source
    crop_job(plan, actor, state)
    status = layout_evaluation_states([plan])[str(plan.pk)]
    assert status["state"] == expected
    if state == "pending":
        assert "has not run" in status["label"]
    if state == "completed":
        assert "no readable room dimensions" in status["label"]
    assert status["canRun"] is (state not in {"running", "cancelled"})


def test_no_job_is_not_claimed_as_completed(source):
    plan, _ = source
    assert layout_evaluation_states([plan])[str(plan.pk)]["state"] == "not_queued"


def test_single_run_reuses_pending_job_only(source, monkeypatch):
    plan, actor = source
    job, _ = crop_job(plan, actor)
    runner = Mock(return_value={"processed": 1, "retry": 0, "failed": 0})
    monkeypatch.setattr("platform_api.workers.process_pending_outbox", runner)
    result = run_single_layout(plan.project, plan.pk, actor)
    assert OutboxMessage.objects.count() == 1
    assert result["status"] == "queued" and result["messageId"] == str(job.pk)
    runner.assert_not_called()


def test_explicit_retry_rearms_failed_job_without_new_plan(source, monkeypatch):
    plan, actor = source
    job, operation = crop_job(plan, actor, "failed")
    OutboxMessage.objects.filter(pk=job.pk).update(dead_lettered_at=timezone.now(), attempts=5)
    runner = Mock(return_value={"processed": 1, "retry": 0, "failed": 0})
    monkeypatch.setattr("platform_api.workers.process_pending_outbox", runner)
    queue_single_layout(plan.project, plan.pk, actor, retry=True)
    job.refresh_from_db()
    operation.refresh_from_db()
    assert job.dead_lettered_at is None and job.attempts == 0
    assert operation.state == "pending"
    assert FloorPlan.objects.count() == OutboxMessage.objects.count() == 1
    runner.assert_not_called()


def test_cancelled_job_never_restarts(source, monkeypatch):
    plan, actor = source
    crop_job(plan, actor, "cancelled")
    runner = Mock()
    monkeypatch.setattr("platform_api.workers.process_pending_outbox", runner)
    assert run_single_layout(plan.project, plan.pk, actor, retry=True, force=True)["status"] == "cancelled"
    runner.assert_not_called()


def test_card_template_shows_pending_and_friendly_title(source):
    from django.test import Client
    from .workspace_helpers import workspace_request,frontend_source
    plan,actor=source
    crop_job(plan,actor)
    plan.plan_name=f'Tower A Unit 1 [manual {uuid4()}]';plan.save()
    client=Client();client.force_login(actor)
    items=workspace_request(client,plan.project,'/media').json()['items']
    item=next(item for item in items if item['planId']==str(plan.pk))
    assert item['label']=='Tower A Unit 1'
    assert item['evaluation']['state']=='queued'
    ui=frontend_source();assert 'pending extraction' in ui and 'evaluationAction' in ui and 'Edit media title' in ui


def test_missed_page_shortcut_opens_cropper_without_queue(source):
    from django.test import Client
    from .workspace_helpers import workspace_request,frontend_source
    plan,actor=source
    client=Client();client.force_login(actor)
    response=client.get(reverse('admin:platform_api_project_add_source_pages',args=[plan.project_id]))
    assert response.status_code==302 and str(plan.project_id) in response.url
    page=workspace_request(client,plan.project,f'/media/documents/{plan.document_id}/pages/1?sha256={plan.document.sha256}')
    assert page.status_code==200
    assert 'Crop source editor' in frontend_source()
    assert not OutboxMessage.objects.exists()
