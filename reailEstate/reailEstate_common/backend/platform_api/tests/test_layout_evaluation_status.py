"""Queue status must explain why dimensions are empty. No live provider calls."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
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
    plan, actor = source
    crop_job(plan, actor)
    plan.plan_name = f"Tower A Unit 1 [manual {uuid4()}]"
    plan.save()
    request = RequestFactory().get("/?section=layouts&include_candidates=1")
    request.user = actor
    response = ProjectAdmin(Project, admin.site).media_dashboard_view(request, str(plan.project_id))
    response.render()
    html = response.content.decode()
    assert "<h2>Tower A Unit 1</h2>" in html
    assert "Room dimensions are pending extraction" in html
    assert "No room dimensions were extracted" not in html
    assert 'value="save_layout_name"' in html
    assert 'value="run_layout_evaluation"' in html


def test_missed_page_shortcut_opens_cropper_without_queue(source):
    plan, actor = source
    request = RequestFactory().post("/", {"action": "mark_layout_page", "document_id": str(plan.document_id), "page_number": 1})
    request.user = actor
    response = ProjectAdmin(Project, admin.site).media_dashboard_view(request, str(plan.project_id))
    assert response.status_code == 302
    assert reverse("admin:platform_api_project_add_source_pages", args=[plan.project_id]) in response.url
    assert "category=layouts" in response.url and "page_number=1" in response.url
    assert not OutboxMessage.objects.exists()