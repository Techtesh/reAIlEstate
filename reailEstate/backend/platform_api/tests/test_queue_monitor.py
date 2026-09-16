def test_admin_monitor_and_user_queue_launch(client, django_user_model):
    from django.urls import reverse
    owner = django_user_model.objects.create_superuser(username="browser", password=None)
    client.force_login(owner)
    page = client.get(reverse("admin:platform_api_project_jobs"))
    assert page.status_code == 200
    assert b"queue-monitor" in page.content
    response = client.get(reverse("admin:platform_api_project_jobs_status"))
    assert response.status_code == 200
    assert "private" in response["Cache-Control"] and "no-store" in response["Cache-Control"]
    with patch("platform_api.queue_monitor.start_user_queue") as launch:
        response = client.post(reverse("admin:platform_api_project_run_reanalysis_cart"))
    assert response.status_code == 302
    launch.assert_called_once_with(owner)

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.utils import timezone

from platform_api.models import Operation, OutboxMessage, QueueWorker
from platform_api.queue_monitor import owned_jobs, queue_snapshot
from .test_floor_plan_layout_jobs import source


pytestmark = pytest.mark.django_db(transaction=True)


def make_job(owner):
    operation = Operation.objects.create(owner=owner, kind="floor_plan_crop_evaluation")
    return OutboxMessage.objects.create(event_type="floor_plan.crop_evaluation.requested",
        correlation_id=uuid4(), payload={"operationId": str(operation.pk)})


def test_owner_queue_does_not_include_other_users(django_user_model):
    first = django_user_model.objects.create_superuser(username="first", password=None)
    second = django_user_model.objects.create_superuser(username="second", password=None)
    own = make_job(first)
    make_job(second)
    assert list(owned_jobs(first)) == [own]


def test_drain_includes_new_children_and_more_than_one_batch(django_user_model):
    owner = django_user_model.objects.create_superuser(username="drain", password=None)
    outsider = django_user_model.objects.create_superuser(username="other", password=None)
    own = make_job(owner)
    foreign = make_job(outsider)
    seen = []

    def process(limit, message_ids):
        seen.extend(message_ids)
        OutboxMessage.objects.filter(pk__in=message_ids).update(published_at=timezone.now())
        if own.pk in message_ids:
            make_job(owner)
        return {"processed": len(message_ids), "retry": 0, "failed": 0}

    with patch("platform_api.management.commands.process_outbox.process_pending_outbox", side_effect=process), \
            patch("platform_api.management.commands.process_outbox.time.sleep"):
        call_command("process_outbox", event_type="extraction", owner_id=owner.pk, drain=True, limit=1)
    assert len(seen) == 2 and foreign.pk not in seen
    assert QueueWorker.objects.get().state == "stopped"


def test_snapshot_marks_stale_workers_and_enforces_permissions(django_user_model):
    owner = django_user_model.objects.create_superuser(username="monitor", password=None)
    job = make_job(owner)
    worker = QueueWorker.objects.create(owner=owner, state="running", message_id=job.pk)
    snapshot = queue_snapshot(owner)
    assert snapshot["counts"]["running"] == 1
    assert snapshot["capacity"]["active"] == 1
    assert snapshot["capacity"]["available"] == snapshot["capacity"]["limit"] - 1
    QueueWorker.objects.filter(pk=worker.pk).update(heartbeat_at=timezone.now() - timedelta(minutes=2))
    snapshot = queue_snapshot(owner)
    assert snapshot["counts"]["running"] == 0
    assert snapshot["workers"][0]["state"] == "stale"
    ordinary = django_user_model.objects.create_user(username="ordinary", password=None)
    with pytest.raises(PermissionDenied):
        queue_snapshot(ordinary)


def test_project_drain_includes_children_but_not_other_projects(django_user_model):
    from platform_api.models import Organization, Project

    owner = django_user_model.objects.create_superuser(username="project-runner", password=None)
    organization = Organization.objects.create(legal_name="Projects")
    project = Project.objects.create(organization=organization, submitted_name="Selected")
    other = Project.objects.create(organization=organization, submitted_name="Other")

    def job_for(target):
        job = make_job(owner)
        job.payload["projectId"] = str(target.pk)
        job.save()
        return job

    first = job_for(project)
    foreign = job_for(other)
    seen = []

    def process(limit, message_ids):
        seen.extend(message_ids)
        OutboxMessage.objects.filter(pk__in=message_ids).update(published_at=timezone.now())
        if first.pk in message_ids:
            job_for(project)
        return {"processed": len(message_ids), "retry": 0, "failed": 0}

    with patch("platform_api.management.commands.process_outbox.process_pending_outbox", side_effect=process), \
            patch("platform_api.management.commands.process_outbox.time.sleep"):
        call_command("process_outbox", event_type="extraction", owner_id=owner.pk,
                     project_id=str(project.pk), drain=True, limit=1)
    assert len(seen) == 2 and foreign.pk not in seen
    assert OutboxMessage.objects.get(pk=foreign.pk).published_at is None


@pytest.mark.parametrize("attempts", [1, 2, 3])
def test_retry_budget_deadletters_after_third_failure(django_user_model, attempts):
    from platform_api.workers import _record_outbox_failure

    owner = django_user_model.objects.create_superuser(username="retry-budget", password=None)
    job = make_job(owner)
    job.attempts = attempts
    job.save()
    _record_outbox_failure(job.pk, RuntimeError(), expected_attempts=attempts)
    job.refresh_from_db()
    operation = Operation.objects.get(pk=job.payload["operationId"])
    assert bool(job.dead_lettered_at) == (attempts > 2)
    assert bool(job.available_after) == (attempts <= 2)
    assert operation.result["retryable"] == (attempts <= 2)


def test_already_exhausted_job_is_not_dispatched(django_user_model):
    from platform_api.workers import process_outbox_message

    owner = django_user_model.objects.create_superuser(username="exhausted", password=None)
    job = make_job(owner)
    job.attempts = 3
    job.save()
    with patch("platform_api.workers._dispatch_outbox_message") as dispatch:
        assert process_outbox_message(job.pk) == "not_ready"
    dispatch.assert_not_called()
    job.refresh_from_db()
    assert job.attempts == 3 and job.dead_lettered_at and not job.available_after


def test_project_launch_waits_for_capacity_and_dispatches_after_worker_stops(settings, django_user_model, tmp_path):
    from platform_api.models import Organization, Project
    from platform_api.queue_monitor import start_user_queue, worker_session

    settings.DEBUG = True
    settings.ENVIRONMENT = "local"
    settings.WORKSPACE_DATA_ROOT = tmp_path
    settings.INGESTION_MAX_WORKERS = 1
    owner = django_user_model.objects.create_superuser(username="capacity", password=None)
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Capacity"), submitted_name="Waiting")
    job = make_job(owner)
    job.payload["projectId"] = str(project.pk)
    job.save()
    with patch("platform_api.queue_monitor.subprocess.Popen") as launch:
        launch.return_value.pid = 1234
        with worker_session("extraction", owner):
            waiting = start_user_queue(owner, project=project)
            assert waiting.state == "queued"
            assert start_user_queue(owner, project=project).pk == waiting.pk
            launch.assert_not_called()
        waiting.refresh_from_db()
        assert waiting.state == "starting" and waiting.pid == 1234
        launch.assert_called_once()
        command = launch.call_args.args[0]
        assert command[command.index("--project-id") + 1] == str(project.pk)
        assert start_user_queue(owner, project=project).pk == waiting.pk
        launch.assert_called_once()


@pytest.mark.parametrize("state,expected", [("starting", "starting or already running"), ("queued", "had to be queued"), ("failed", "Could not start")])
def test_project_run_action_reports_capacity(client, django_user_model, state, expected):
    from django.contrib.messages import get_messages
    from django.urls import reverse
    from platform_api.models import Organization, Project

    owner = django_user_model.objects.create_superuser(username="project-button", password=None)
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Button"), submitted_name="Selected")
    client.force_login(owner)
    url = reverse("admin:platform_api_project_media_dashboard", args=[project.pk])
    with patch("platform_api.queue_monitor.start_user_queue", return_value=QueueWorker(state=state)) as launch:
        response = client.post(url + "?section=floor-plans", {"action": "run_project_evaluations"})
    launch.assert_called_once_with(owner, project=project)
    assert response.status_code == 302 and response.url.endswith("?section=floor-plans")
    assert expected in " ".join(str(message) for message in get_messages(response.wsgi_request))


def test_dead_letter_delete_is_confirmed_audited_and_preserves_operation(client, django_user_model):
    from django.core.exceptions import ValidationError
    from django.urls import reverse
    from platform_api.models import AuditEvent
    from platform_api.queue_monitor import delete_dead_letter

    owner = django_user_model.objects.create_superuser(username="delete-job", password=None)
    job = make_job(owner)
    job.dead_lettered_at = timezone.now()
    job.attempts = 3
    job.save()
    snapshot = queue_snapshot(owner)
    row = next(row for row in snapshot["jobs"] if row["id"] == str(job.pk))
    assert row["state"] == "dead_letter" and row["canDelete"]
    assert snapshot["counts"]["dead_letter"] == 1
    with pytest.raises(ValidationError):
        delete_dead_letter(owner, job.pk, row["deleteVersion"])
    with pytest.raises(ValidationError):
        delete_dead_letter(owner, job.pk, "stale", confirmed=True)
    client.force_login(owner)
    response = client.post(reverse("admin:platform_api_project_jobs"), {"action": "delete_dead_letter",
        "message_id": str(job.pk), "version": row["deleteVersion"], "confirm_delete": "yes"})
    assert response.status_code == 302
    assert not OutboxMessage.objects.filter(pk=job.pk).exists()
    assert Operation.objects.filter(pk=job.payload["operationId"]).exists()
    assert AuditEvent.objects.filter(action="queue.dead_letter_deleted", entity_id=job.pk, actor=owner).exists()


@pytest.mark.parametrize("state", ["pending", "completed", "running", "retried"])
def test_dead_letter_delete_rejects_nonterminal_or_retried_job(django_user_model, state):
    from django.core.exceptions import ValidationError
    from platform_api.queue_monitor import delete_dead_letter

    owner = django_user_model.objects.create_superuser(username="protected-job", password=None)
    job = make_job(owner)
    version = timezone.now()
    if state in {"completed", "running"}:
        job.dead_lettered_at = version
    if state == "completed":
        job.published_at = timezone.now()
    if state == "running":
        QueueWorker.objects.create(owner=owner, state="running", message_id=job.pk)
    job.save()
    with pytest.raises(ValidationError):
        delete_dead_letter(owner, job.pk, version.isoformat(), confirmed=True)
    assert OutboxMessage.objects.filter(pk=job.pk).exists()


def test_dead_letter_delete_requires_permission_and_csrf(client, django_user_model):
    from django.test import Client
    from django.urls import reverse
    from platform_api.queue_monitor import delete_dead_letter

    owner = django_user_model.objects.create_superuser(username="csrf-owner", password=None)
    ordinary = django_user_model.objects.create_user(username="not-allowed", password=None, is_staff=True)
    job = make_job(owner)
    job.dead_lettered_at = timezone.now()
    job.save()
    with pytest.raises(PermissionDenied):
        delete_dead_letter(ordinary, job.pk, job.dead_lettered_at.isoformat(), confirmed=True)
    protected_client = Client(enforce_csrf_checks=True)
    protected_client.force_login(owner)
    response = protected_client.post(reverse("admin:platform_api_project_jobs"), {"action": "delete_dead_letter",
        "message_id": str(job.pk), "version": job.dead_lettered_at.isoformat(), "confirm_delete": "yes"})
    assert response.status_code == 403 and OutboxMessage.objects.filter(pk=job.pk).exists()


def test_worker_skips_a_job_deleted_after_batch_selection():
    from platform_api.workers import process_outbox_message

    with patch("platform_api.workers._dispatch_outbox_message") as dispatch:
        assert process_outbox_message(uuid4()) == "not_ready"
    dispatch.assert_not_called()


def test_failed_project_run_requires_explicit_confirmation(client, django_user_model):
    from django.urls import reverse
    from platform_api.models import Organization, Project

    owner = django_user_model.objects.create_superuser(username="retry-warning", password=None)
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Warnings"),
                                     submitted_name="Adityaraj empire b wing")
    job = make_job(owner)
    job.payload["projectId"] = str(project.pk)
    job.attempts = 1
    job.last_error_code = "PROVIDER_UNAVAILABLE"
    job.save()
    client.force_login(owner)
    url = reverse("admin:platform_api_project_media_dashboard", args=[project.pk])
    submitted = {"action": "run_project_evaluations"}
    with patch("platform_api.queue_monitor.start_user_queue", return_value=QueueWorker(state="starting")) as launch:
        response = client.post(url, submitted, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        assert response.status_code == 409
        warning = response.json()
        assert warning["project"] == project.submitted_name
        assert warning["failures"][0]["error"] == "PROVIDER_UNAVAILABLE"
        launch.assert_not_called()
        job.refresh_from_db()
        assert job.attempts == 1 and job.last_error_code == "PROVIDER_UNAVAILABLE"
        response = client.post(url, {**submitted, "retry_confirmation": warning["confirmationToken"]},
                       HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        assert response.status_code == 200 and response.json()["redirectUrl"].startswith(url)
        launch.assert_called_once_with(owner, project=project)


@pytest.mark.parametrize("action,event_type", [
    ("queue_project_reanalysis", "document.processing.requested"),
    ("queue_failed_reanalysis", "document.processing.requested"),
    ("run_project_evaluations", "floor_plan.crop_evaluation.requested"),
    ("run_layout_evaluation", "floor_plan.crop_evaluation.requested"),
    ("queue_floor_layouts", "floor_plan.units_evaluation.requested"),
    ("queue_amenity_pages", "source.page_evaluation.requested"),
])
def test_retry_gate_blocks_mutation_for_each_dashboard_action(source, client, action, event_type):
    from django.urls import reverse

    plan, owner = source
    plan.document.status = "extraction_failed"
    plan.document.save()
    plan.asset.source_bounds = {"manualPageAddition": True, "category": "amenities"}
    plan.asset.save()
    job = make_job(owner)
    job.event_type = event_type
    job.payload.update(projectId=str(plan.project_id), documentId=str(plan.document_id),
                       floorPlanId=str(plan.pk), assetId=str(plan.asset_id))
    job.dead_lettered_at = timezone.now()
    job.attempts = 3
    job.last_error_code = "PROVIDER_UNAVAILABLE"
    job.save()
    before = OutboxMessage.objects.values().get(pk=job.pk)
    client.force_login(owner)
    response = client.post(reverse("admin:platform_api_project_media_dashboard", args=[plan.project_id]),
        {"action": action, "floor_plan_id": str(plan.pk), "retry": "true"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert response.status_code == 409 and response.json()["requiresConfirmation"]
    assert OutboxMessage.objects.values().get(pk=job.pk) == before
    assert OutboxMessage.objects.count() == 1 and not QueueWorker.objects.exists()
    targets = {
        "queue_project_reanalysis": ("platform_api.admin.queue_project_reanalysis", []),
        "queue_failed_reanalysis": ("platform_api.admin.queue_project_reanalysis", []),
        "run_project_evaluations": ("platform_api.queue_monitor.start_user_queue", QueueWorker(state="starting")),
        "run_layout_evaluation": ("platform_api.layout_evaluation_status.queue_single_layout", {"status": "queued"}),
        "queue_floor_layouts": ("platform_api.admin.queue_floor_plan_layouts", job),
        "queue_amenity_pages": ("platform_api.source_page_additions.queue_saved_amenity_pages", []),
    }
    target, result = targets[action]
    with patch(target, return_value=result) as handler:
        confirmed = client.post(reverse("admin:platform_api_project_media_dashboard", args=[plan.project_id]),
            {"action": action, "floor_plan_id": str(plan.pk), "retry": "true",
             "retry_confirmation": response.json()["confirmationToken"]}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert confirmed.status_code == 200 and "redirectUrl" in confirmed.json()
    handler.assert_called_once()


@pytest.mark.parametrize("change", ["attempts", "action", "actor", "tampered", "expired"])
def test_retry_gate_rejects_stale_or_mismatched_confirmation(source, django_user_model, change):
    from django.http import QueryDict
    from platform_api.reanalysis import evaluation_retry_warning
    import time

    plan, owner = source
    job = make_job(owner)
    job.payload["floorPlanId"] = str(plan.pk)
    job.last_error_code = "PROVIDER_UNAVAILABLE"
    job.attempts = 1
    job.save()
    submitted = QueryDict("", mutable=True)
    submitted["action"] = "run_project_evaluations"
    warning = evaluation_retry_warning(plan.project, owner, submitted)
    submitted["retry_confirmation"] = warning["confirmationToken"]
    if change == "attempts":
        OutboxMessage.objects.filter(pk=job.pk).update(attempts=2)
    elif change == "action":
        submitted["action"] = "run_layout_evaluation"
        submitted["floor_plan_id"] = str(plan.pk)
    elif change == "actor":
        owner = django_user_model.objects.create_superuser(username="different-reviewer", password=None)
    elif change == "tampered":
        submitted["retry_confirmation"] += "forged"
    with patch("django.core.signing.time.time", return_value=time.time() + (301 if change == "expired" else 0)):
        assert evaluation_retry_warning(plan.project, owner, submitted) is not None


def test_retry_history_survives_dead_letter_deletion_and_is_project_scoped(source):
    from django.http import QueryDict
    from platform_api.models import Project
    from platform_api.queue_monitor import delete_dead_letter
    from platform_api.reanalysis import evaluation_retry_warning

    plan, owner = source
    job = make_job(owner)
    job.payload["floorPlanId"] = str(plan.pk)
    job.dead_lettered_at = timezone.now()
    job.save()
    Operation.objects.filter(pk=job.payload["operationId"]).update(state="failed", error_code="PROVIDER_UNAVAILABLE",
                                                                 result={"floorPlanId": str(plan.pk)})
    delete_dead_letter(owner, job.pk, job.dead_lettered_at.isoformat(), confirmed=True)
    submitted = QueryDict("action=run_project_evaluations")
    warning = evaluation_retry_warning(plan.project, owner, submitted)
    assert warning["failures"][0]["label"] == plan.display_title
    other = Project.objects.create(organization=plan.project.organization, submitted_name="Unrelated")
    assert evaluation_retry_warning(other, owner, submitted) is None


def test_retry_warning_fallback_escapes_names_and_preserves_form(source, client):
    from django.urls import reverse

    plan, owner = source
    plan.project.submitted_name = '<script>alert("project")</script>'
    plan.project.save()
    job = make_job(owner)
    job.payload["projectId"] = str(plan.project_id)
    job.last_error_code = "PROVIDER_UNAVAILABLE"
    job.save()
    client.force_login(owner)
    url = reverse("admin:platform_api_project_media_dashboard", args=[plan.project_id])
    response = client.post(url, {"action": "run_project_evaluations"})
    assert response.status_code == 409
    html = response.content.decode()
    assert '<script>alert("project")</script>' not in html
    assert "&lt;script&gt;" in html and 'name="retry_confirmation"' in html
    assert 'name="action" value="run_project_evaluations"' in html
    assert not QueueWorker.objects.exists()