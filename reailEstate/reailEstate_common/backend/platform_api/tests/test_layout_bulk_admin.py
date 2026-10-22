"""Bulk service and opt-in admin contracts; no production admin registration."""

from contextlib import contextmanager
from datetime import timedelta
from threading import Event, Thread
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.contrib.admin import AdminSite, ModelAdmin
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections, connection, transaction
from django.http import HttpResponse
from django.test import Client
from django.urls import path, reverse
from django.utils import timezone

from platform_api import floor_plan_removal as removal
from platform_api.ingestion_runtime import WorkAlreadyClaimed
from platform_api.layout_bulk_admin import CONFIRMATION_MAX_AGE, SELECTION_SALT, LayoutBulkAdminMixin
from platform_api.models import AuditEvent, EvidenceDocument, FloorPlan, FloorPlanAsset, Operation, OutboxMessage, Project, ProjectMerge, StationCandidate
from platform_api.tests.layout_bulk_helpers import layout_records, queue_plan, versions

pytestmark = pytest.mark.django_db(transaction=True)


class BulkTestAdmin(LayoutBulkAdminMixin, ModelAdmin):
    def get_urls(self):
        return self.layout_bulk_urls() + [path(
            "<uuid:object_id>/media/", self.admin_site.admin_view(lambda request, object_id: HttpResponse("Layouts")),
            name="platform_api_project_media_dashboard",
        ), path("jobs/", self.admin_site.admin_view(lambda request: HttpResponse("Jobs")),
                name="platform_api_project_jobs"),
            path("layout-cart/", self.admin_site.admin_view(lambda request: HttpResponse("Cart")),
                 name="platform_api_project_layout_cart")] + super().get_urls()


site = AdminSite(name="bulk_test")
site.register(Project, BulkTestAdmin)
site.register(StationCandidate, ModelAdmin)
urlpatterns = [path("admin/", site.urls)]


@pytest.fixture
def bulk_client(layout_records, client, settings):
    settings.ROOT_URLCONF = __name__
    actor, project, _ = layout_records
    client.force_login(actor)
    url = reverse("bulk_test:platform_api_project_layout_bulk_remove", args=[project.pk])
    return client, url


def confirmation_data(response):
    form = response.context["form"]
    # Exercise the actual hidden JSON widget serialization (not Python repr).
    return {"stage": "confirm", "confirm": "on", "selection_token": form["selection_token"].value(),
            "expected_updated_at": form["expected_updated_at"].value()}


def test_batch_preserves_evidence_individual_audits_and_restore(layout_records, monkeypatch):
    actor, project, make = layout_records
    first = make(state="needs_boundary_review", raw={"qualityWarning": "Wrong boundary"})
    second = make(document=first.document, state="approved")
    retained = make(document=first.document)
    # Shared images are evidence, not removal targets.
    second.asset = first.asset
    second.save()
    jobs = [queue_plan(plan, actor, event) for plan in (first, second) for event in removal.PLAN_JOB_EVENTS]
    unrelated, unrelated_operation = queue_plan(retained, actor)
    document_job, document_operation = queue_plan(first, actor, "document.processing.requested")
    original_counts = (EvidenceDocument.objects.count(), FloorPlanAsset.objects.count(), FloorPlan.objects.count())
    original_raw = first.raw_extraction
    monkeypatch.setattr("platform_api.artifact_storage.storage.delete", Mock(side_effect=AssertionError("Deleted evidence")))
    result = removal.remove_floor_plans(project, [first.pk, second.pk, first.pk], actor,
                                       expected_updated_at=versions(first, second))
    assert (result.selected_count, result.removed_count, result.unchanged_count, result.cancelled_job_count) == (2, 2, 0, 6)
    for plan, prior in [(first, "needs_boundary_review"), (second, "approved")]:
        plan.refresh_from_db()
        assert plan.review_state == "removed_by_user"
        audit = AuditEvent.objects.get(entity_id=plan.pk, action="floor_plan.removed_by_user")
        assert audit.metadata["previousReviewState"] == prior
        assert len(audit.metadata["cancelledJobIds"]) == 3
        removal.set_floor_plan_removed(project, plan.pk, actor, removed=False)
        plan.refresh_from_db()
        assert plan.review_state == prior
    for job, operation in jobs:
        job.refresh_from_db()
        operation.refresh_from_db()
        assert job.dead_lettered_at and operation.state == "cancelled"
    for job, operation in [(unrelated, unrelated_operation), (document_job, document_operation)]:
        job.refresh_from_db()
        operation.refresh_from_db()
        assert job.dead_lettered_at is None and operation.state == "pending"
    assert first.raw_extraction == original_raw
    assert original_counts == (EvidenceDocument.objects.count(), FloorPlanAsset.objects.count(), FloorPlan.objects.count())


def test_batch_does_not_cancel_shared_operation_or_child(layout_records):
    actor, project, make = layout_records
    plan = make()
    child = make(document=plan.document, raw={"sourcePlanId": str(plan.pk)})
    selected_job, operation = queue_plan(plan, actor)
    child_job, _ = queue_plan(child, actor, operation=operation)
    removal.remove_floor_plans(project, [plan.pk], actor, expected_updated_at=versions(plan))
    operation.refresh_from_db()
    child_job.refresh_from_db()
    selected_job.refresh_from_db()
    child.refresh_from_db()
    assert operation.state == "pending" and child_job.dead_lettered_at is None
    assert child.review_state == "needs_review" and selected_job.dead_lettered_at


@pytest.mark.parametrize("bad_ids", [[], ["invalid"], [str(uuid4())] * 201])
def test_bounded_selection_rejected_before_writes(layout_records, bad_ids):
    actor, project, _ = layout_records
    with pytest.raises(ValueError):
        removal.remove_floor_plans(project, bad_ids, actor, expected_updated_at={})
    assert not AuditEvent.objects.exists()


def test_exact_limit_is_accepted_without_unbounded_consumption():
    ids = [uuid4() for _ in range(200)]
    assert len(removal.normalize_plan_ids(ids)) == 200
    assert len(removal.normalize_plan_ids([ids[0], str(ids[0]).upper()])) == 1


@pytest.mark.parametrize("problem", ["stale", "hidden", "floor", "missing_timestamp", "extra_timestamp", "foreign"])
def test_batch_preflight_is_all_or_nothing(layout_records, problem):
    actor, project, make = layout_records
    first, second = make(), make()
    expected = versions(first, second)
    if problem == "stale":
        FloorPlan.objects.filter(pk=second.pk).update(updated_at=second.updated_at + timedelta(seconds=1))
    elif problem == "hidden":
        FloorPlan.objects.filter(pk=second.pk).update(review_state="replaced_by_user")
    elif problem == "floor":
        FloorPlan.objects.filter(pk=second.pk).update(plan_scope="floor")
    elif problem == "missing_timestamp":
        expected.pop(str(second.pk))
    elif problem == "extra_timestamp":
        expected[str(uuid4())] = timezone.now()
    else:
        other = Project.objects.create(organization=project.organization, submitted_name="Unrelated")
        FloorPlan.objects.filter(pk=second.pk).update(project=other)
    job, operation = queue_plan(first, actor)
    with pytest.raises((ValueError, FloorPlan.DoesNotExist)):
        removal.remove_floor_plans(project, [first.pk, second.pk], actor, expected_updated_at=expected)
    first.refresh_from_db()
    job.refresh_from_db()
    operation.refresh_from_db()
    assert first.review_state == "needs_review" and job.dead_lettered_at is None and operation.state == "pending"
    assert not AuditEvent.objects.exists()


def test_batch_audit_failure_rolls_back_prior_plan_and_all_job_updates(layout_records, monkeypatch):
    actor, project, make = layout_records
    plans = sorted([make(), make()], key=lambda plan: str(plan.pk))
    jobs = [queue_plan(plan, actor) for plan in plans]
    real_create = AuditEvent.objects.create
    calls = []

    def fail_second(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise RuntimeError("Audit write failed")
        return real_create(**kwargs)

    monkeypatch.setattr(AuditEvent.objects, "create", fail_second)
    with pytest.raises(RuntimeError, match="Audit write failed"):
        removal.remove_floor_plans(project, [plan.pk for plan in plans], actor, expected_updated_at=versions(*plans))
    assert FloorPlan.objects.filter(review_state="removed_by_user").count() == 0
    assert not AuditEvent.objects.exists()
    for job, operation in jobs:
        job.refresh_from_db()
        operation.refresh_from_db()
        assert job.dead_lettered_at is None and operation.state == "pending"


@pytest.mark.parametrize("namespace", ["document", "outbox"])
def test_sorted_locks_once_before_transaction_and_busy_rollback(layout_records, monkeypatch, namespace):
    actor, project, make = layout_records
    first = make()
    plans = [first, make(document=first.document), make()]
    jobs = [queue_plan(plan, actor)[0] for plan in plans]
    expected_calls = [("document", pk) for pk in sorted({plan.document_id for plan in plans}, key=str)]
    expected_calls += [("outbox", job.pk) for job in sorted(jobs, key=lambda job: str(job.pk))]
    busy_key = [key for key in expected_calls if key[0] == namespace][-1]
    calls, released = [], []

    @contextmanager
    def lock(kind, pk):
        assert not connection.in_atomic_block
        calls.append((kind, pk))
        if (kind, pk) == busy_key:
            raise WorkAlreadyClaimed("busy")
        try:
            yield
        finally:
            released.append((kind, pk))

    monkeypatch.setattr(removal, "work_lock", lock)
    with pytest.raises(WorkAlreadyClaimed):
        removal.remove_floor_plans(project, [plan.pk for plan in reversed(plans)], actor, expected_updated_at=versions(*plans))
    assert calls == expected_calls[:expected_calls.index(busy_key) + 1]
    assert released == list(reversed(calls[:-1]))
    assert not FloorPlan.objects.filter(review_state="removed_by_user").exists()
    assert not AuditEvent.objects.exists()


def test_job_inserted_after_discovery_forces_retry_then_is_cancelled(layout_records, monkeypatch):
    actor, project, make = layout_records
    plan = make()
    old_job, _ = queue_plan(plan, actor)
    inserted = []

    @contextmanager
    def lock(kind, pk):
        if kind == "outbox" and pk == old_job.pk and not inserted:
            inserted.append(queue_plan(plan, actor)[0])
        yield

    monkeypatch.setattr(removal, "work_lock", lock)
    with pytest.raises(WorkAlreadyClaimed):
        removal.remove_floor_plans(project, [plan.pk], actor, expected_updated_at=versions(plan))
    plan.refresh_from_db()
    assert plan.review_state == "needs_review"
    assert not OutboxMessage.objects.filter(dead_lettered_at__isnull=False).exists()
    result = removal.remove_floor_plans(project, [plan.pk], actor, expected_updated_at=versions(plan))
    assert result.cancelled_job_count == 2


@pytest.mark.parametrize("locked_model", [EvidenceDocument, FloorPlan, OutboxMessage, Operation])
def test_postgres_queue_row_contention_fails_fast_without_document_work_lock(layout_records, locked_model):
    if connection.vendor != "postgresql":
        pytest.skip("Real row-lock contention requires PostgreSQL.")
    actor, project, make = layout_records
    plan = make()
    job, operation = queue_plan(plan, actor)
    pk = {EvidenceDocument: plan.document_id, FloorPlan: plan.pk, OutboxMessage: job.pk, Operation: operation.pk}[locked_model]
    ready, release = Event(), Event()
    errors = []

    def row_only_queue_writer():
        close_old_connections()
        try:
            with transaction.atomic():
                locked_model.objects.select_for_update().get(pk=pk)
                ready.set()
                if not release.wait(10):
                    raise AssertionError("Timed out releasing queue row lock")
        except Exception as exc:
            errors.append(exc)
            ready.set()
        finally:
            close_old_connections()

    thread = Thread(target=row_only_queue_writer)
    thread.start()
    try:
        assert ready.wait(10) and not errors
        with pytest.raises(WorkAlreadyClaimed):
            removal.remove_floor_plans(project, [plan.pk], actor, expected_updated_at=versions(plan))
    finally:
        release.set()
        thread.join(15)
    assert not thread.is_alive() and not errors
    plan.refresh_from_db()
    job.refresh_from_db()
    assert plan.review_state == "needs_review" and job.dead_lettered_at is None
    assert not AuditEvent.objects.exists()


def test_admin_get_query_ids_and_initial_post_never_mutate(layout_records, bulk_client):
    _, _, make = layout_records
    client, url = bulk_client
    plan = make()
    response = client.get(url, {"selected_ids": str(plan.pk), "confirm": "on"})
    assert response.status_code == 200 and response.context["selected_count"] == 0
    response = client.post(url, {"selected_ids": [str(plan.pk)], "confirm": "on", "select_all": "on"})
    assert response.status_code == 200 and response.context["selected_count"] == 1
    assert b"csrfmiddlewaretoken" in response.content and b'name="expected_updated_at"' in response.content
    assert b"onclick=" not in response.content and b"onsubmit=" not in response.content
    plan.refresh_from_db()
    assert plan.review_state == "needs_review" and not AuditEvent.objects.exists()


def test_admin_confirmation_exact_selection_counts_redirect_and_replay(layout_records, bulk_client):
    actor, project, make = layout_records
    client, url = bulk_client
    selected, retained = make(), make()
    queue_plan(selected, actor)
    response = client.post(url, {"selected_ids": [str(selected.pk)]})
    data = confirmation_data(response)
    response = client.post(url, data)
    assert response.status_code == 302
    assert response.url == reverse("bulk_test:platform_api_project_media_dashboard", args=[project.pk]) + "?section=layouts"
    assert "Selected 1; removed 1; unchanged 0; cancelled 1" in str(list(get_messages(response.wsgi_request))[-1])
    retained.refresh_from_db()
    assert retained.review_state == "needs_review"
    assert client.post(url, data).status_code == 409
    assert AuditEvent.objects.filter(action="floor_plan.removed_by_user").count() == 1


@pytest.mark.parametrize("problem", ["checkbox", "missing_token", "tampered_token", "timestamp", "stale", "expired", "busy"])
def test_admin_invalid_confirmation_cannot_remove(layout_records, bulk_client, monkeypatch, problem):
    _, _, make = layout_records
    client, url = bulk_client
    plan = make()
    response = client.post(url, {"selected_ids": [str(plan.pk)]})
    data = confirmation_data(response)
    if problem == "checkbox":
        data.pop("confirm")
    elif problem == "missing_token":
        data.pop("selection_token")
    elif problem == "tampered_token":
        data["selection_token"] += "tampered"
    elif problem == "timestamp":
        data["expected_updated_at"] = "{}"
    elif problem == "stale":
        plan.display_name = "Changed during confirmation"
        plan.save()
    elif problem == "expired":
        payload = signing.loads(data["selection_token"], salt=SELECTION_SALT)
        signer = signing.TimestampSigner(salt=SELECTION_SALT)
        expired_at = int(timezone.now().timestamp()) - CONFIRMATION_MAX_AGE - 60
        monkeypatch.setattr(signer, "timestamp", lambda: signing.b62_encode(expired_at))
        data["selection_token"] = signer.sign_object(payload, compress=True)
    else:
        monkeypatch.setattr("platform_api.layout_bulk_admin.remove_floor_plans", Mock(side_effect=WorkAlreadyClaimed("busy")))
    assert client.post(url, data).status_code in {400, 409}
    plan.refresh_from_db()
    assert plan.review_state == "needs_review" and not AuditEvent.objects.exists()


@pytest.mark.parametrize("permissions", [[], ["view_project"], ["change_floorplan"]])
def test_bulk_permissions_on_get_post_and_service(layout_records, bulk_client, django_user_model, permissions):
    _, project, make = layout_records
    client, url = bulk_client
    plan = make()
    actor = django_user_model.objects.create_user(username="limited-bulk", is_staff=True)
    actor.user_permissions.add(*Permission.objects.filter(content_type__app_label="platform_api", codename__in=permissions))
    client.force_login(actor)
    assert client.get(url).status_code == 403
    assert client.post(url, {"selected_ids": str(plan.pk)}).status_code == 403
    with pytest.raises(PermissionDenied):
        removal.remove_floor_plans(project, [plan.pk], actor, expected_updated_at=versions(plan))


def test_confirmation_bound_to_actor_and_project(layout_records, bulk_client, django_user_model):
    _, project, make = layout_records
    client, url = bulk_client
    plan = make()
    data = confirmation_data(client.post(url, {"selected_ids": str(plan.pk)}))
    other = Project.objects.create(organization=project.organization, submitted_name="Other")
    other_url = reverse("bulk_test:platform_api_project_layout_bulk_remove", args=[other.pk])
    assert client.post(other_url, data).status_code == 409
    assert client.post(other_url, {"selected_ids": str(plan.pk)}).status_code == 404
    actor = django_user_model.objects.create_superuser(username="different-editor", password="test-only")
    client.force_login(actor)
    assert client.post(url, data).status_code == 409
    assert not AuditEvent.objects.exists()


def test_authorized_staff_csrf_method_and_merged_selection(layout_records, bulk_client, django_user_model):
    _, canonical, make = layout_records
    _, url = bulk_client
    source = Project.objects.create(organization=canonical.organization, submitted_name="Merged evidence", status="merged")
    ProjectMerge.objects.create(source_project=source, target_project=canonical, state="applied")
    plan = make(owner=source)
    actor = django_user_model.objects.create_user(username="authorized-bulk", is_staff=True)
    actor.user_permissions.add(*Permission.objects.filter(content_type__app_label="platform_api",
                                                          codename__in=["view_project", "change_floorplan"]))
    client = Client(enforce_csrf_checks=True)
    client.force_login(actor)
    assert client.post(url, {"selected_ids": str(plan.pk)}).status_code == 403
    client.get(url)
    token = client.cookies["csrftoken"].value
    assert client.put(url, HTTP_X_CSRFTOKEN=token).status_code == 405
    response = client.post(url, {"selected_ids": str(plan.pk), "csrfmiddlewaretoken": token})
    assert response.status_code == 200
    data = confirmation_data(response)
    assert client.post(url, data).status_code == 403
    data["csrfmiddlewaretoken"] = token
    assert client.post(url, data).status_code == 302
    audit = AuditEvent.objects.get(action="floor_plan.removed_by_user")
    assert audit.metadata["projectId"] == str(source.pk) and audit.metadata["dashboardProjectId"] == str(canonical.pk)