import csv
import hashlib
import io
import json
from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from platform_api.admin import FloorPlanAdmin
from platform_api.artifact_storage import storage
from platform_api.floor_plan_removal import PLAN_JOB_EVENTS, REMOVAL_ERROR, set_floor_plan_removed
from platform_api.floor_plan_visibility import visible_plan_assets
from platform_api.ingestion_runtime import WorkAlreadyClaimed
from platform_api.models import (
    AgentWorkflowRun, AuditEvent, DocumentLayoutPage, EvidenceDocument, FloorPlan,
    FloorPlanAsset, IngestionRun, Operation, Organization, OutboxMessage, Project,
    ProjectFact, ProjectMerge,
)
from platform_api.project_outputs import build_project_output, store_outputs
from platform_api.serializers import ProjectDetailSerializer


pytestmark = pytest.mark.django_db


@pytest.fixture
def records(settings, tmp_path, django_user_model):
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.WORKSPACE_DATA_ROOT = tmp_path
    settings.SECURE_SSL_REDIRECT = False
    settings.ALLOWED_HOSTS = ["testserver"]
    actor = django_user_model.objects.create_superuser(username="removal-admin", password="test-only")
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Removal tests"),
                                     submitted_name="Duplicate plans")
    data = b"immutable source evidence"
    document = EvidenceDocument.objects.create(
        project=project, uploaded_by=actor, original_name="source.pdf", scan_status="clean",
        object_key=storage.save(f"sources/{uuid4()}.pdf", ContentFile(data)),
        declared_media_type="application/pdf", byte_length=len(data), sha256=hashlib.sha256(data).hexdigest(),
        retention_until=timezone.now() + timedelta(days=7),
    )
    run = IngestionRun.objects.create(project=project, document=document, status="completed")
    workflow = AgentWorkflowRun.objects.create(project=project, document=document, ingestion_run=run,
        workflow_type="manual_source_page", status="queued", result={"manualPageAddition": True})
    asset = FloorPlanAsset.objects.create(project=project, document=document, workflow_run=workflow,
        page_number=30, asset_type="page_render", object_key=storage.save(f"images/{uuid4()}.png", ContentFile(data)),
        byte_length=len(data), sha256=hashlib.sha256(data).hexdigest(), pixel_width=100, pixel_height=100,
        detector_version="test", source_bounds={"category": "floor-plans", "manualPageAddition": True})
    plans = [FloorPlan.objects.create(project=project, document=document, workflow_run=workflow,
        asset=asset, page_number=30, plan_scope="floor", plan_name=name,
        raw_extraction={"manualPageAddition": True, "sourcePlanName": name},
        review_state="pending_page_evaluation", extractor_version="test") for name in ["Selected", "Retained"]]
    DocumentLayoutPage.objects.create(document=document, page_number=30, marked_by=actor)
    return actor, project, plans[0], plans[1]


def visibility_url(project, plan, action="remove"):
    return reverse("admin:platform_api_floorplan_visibility", args=[plan.pk, project.pk, action])


def confirm(client, url):
    response = client.get(url)
    assert response.status_code == 200
    return client.post(url, {"confirm": "on", "expected_updated_at": response.context["form"].initial["expected_updated_at"]})


def queue(plan, actor, event, operation=None):
    operation = operation or Operation.objects.create(owner=actor, kind="evaluation")
    message = OutboxMessage.objects.create(event_type=event, correlation_id=uuid4(), payload={
        "floorPlanId": str(plan.pk), "assetId": str(plan.asset_id), "documentId": str(plan.document_id),
        "projectId": str(plan.project_id), "operationId": str(operation.pk),
    })
    return message, operation


@pytest.mark.parametrize("scope,section", [("floor", "floor-plans"), ("flat", "layouts")])
def test_confirmed_remove_and_record_restore_preserve_evidence(records, client, scope, section):
    actor, project, selected, retained = records
    FloorPlan.objects.filter(project=project).update(plan_scope=scope)
    selected.refresh_from_db()
    provenance = selected.raw_extraction
    original_keys = (selected.document.object_key, selected.asset.object_key)
    client.force_login(actor)
    url = visibility_url(project, selected)
    dashboard = reverse("admin:platform_api_project_media_dashboard", args=[project.pk])
    response = client.get(dashboard, {"section": section})
    assert url.encode() in response.content  # Both categories have the explicit action.
    response = client.get(url)
    assert b"csrfmiddlewaretoken" in response.content
    assert b"onclick=" not in response.content and b"onsubmit=" not in response.content
    assert client.post(url, {}).status_code == 200
    selected.refresh_from_db()
    assert selected.review_state == "pending_page_evaluation"
    assert not AuditEvent.objects.filter(action="floor_plan.removed_by_user").exists()
    assert confirm(client, url).status_code == 302
    selected.refresh_from_db()
    retained.refresh_from_db()
    assert selected.review_state == "removed_by_user"
    assert retained.review_state == "pending_page_evaluation"
    assert selected.raw_extraction == provenance
    assert FloorPlan.objects.filter(project=project).count() == 2
    assert FloorPlanAsset.objects.filter(project=project).count() == 1
    assert DocumentLayoutPage.objects.get(document=selected.document).state == "layout"
    for key in original_keys:
        with storage.open(key) as source:
            assert source.read() == b"immutable source evidence"
    assert AuditEvent.objects.get(action="floor_plan.removed_by_user").metadata["previousReviewState"] == "pending_page_evaluation"
    response = client.get(dashboard, {"section": section, "include_candidates": "1"})
    cards = response.context["floor_plans" if scope == "floor" else "layouts"]
    assert [card["plan"].pk for card in cards] == [retained.pk]
    assert response.context["carousel"]  # A shared source remains available.
    record = client.get(reverse("admin:platform_api_floorplan_change", args=[selected.pk]))
    restore_url = visibility_url(project, selected, "restore")
    assert restore_url.encode() in record.content
    assert confirm(client, restore_url).status_code == 302
    selected.refresh_from_db()
    assert selected.review_state == "pending_page_evaluation"
    assert selected.raw_extraction == provenance
    assert AuditEvent.objects.filter(action="floor_plan.restored_by_user").count() == 1


@pytest.mark.parametrize("event", PLAN_JOB_EVENTS)
def test_only_selected_jobs_cancel_and_restore_does_not_requeue(records, event):
    actor, project, selected, retained = records
    selected_job, selected_operation = queue(selected, actor, event)
    retained_job, retained_operation = queue(retained, actor, event)
    document_job, document_operation = queue(selected, actor, "document.processing.requested")
    set_floor_plan_removed(project, selected.pk, actor, removed=True)
    selected_job.refresh_from_db()
    selected_operation.refresh_from_db()
    assert selected_job.dead_lettered_at and selected_job.last_error_code == REMOVAL_ERROR
    assert selected_operation.state == "cancelled"
    for job, operation in [(retained_job, retained_operation), (document_job, document_operation)]:
        job.refresh_from_db()
        operation.refresh_from_db()
        assert job.dead_lettered_at is None and operation.state == "pending"
    assert set_floor_plan_removed(project, selected.pk, actor, removed=True)[1] is False
    assert AuditEvent.objects.filter(action="floor_plan.removed_by_user").count() == 1
    set_floor_plan_removed(project, selected.pk, actor, removed=False)
    selected_operation.refresh_from_db()
    assert selected_operation.state == "cancelled"
    assert OutboxMessage.objects.count() == 3


def test_shared_operation_is_not_cancelled(records):
    actor, project, selected, retained = records
    selected_job, operation = queue(selected, actor, PLAN_JOB_EVENTS[0])
    retained_job, _ = queue(retained, actor, PLAN_JOB_EVENTS[0], operation=operation)
    set_floor_plan_removed(project, selected.pk, actor, removed=True)
    operation.refresh_from_db()
    retained_job.refresh_from_db()
    selected_job.refresh_from_db()
    assert operation.state == "pending" and retained_job.dead_lettered_at is None
    assert selected_job.dead_lettered_at is not None


@pytest.mark.parametrize("busy_namespace", ["document", "outbox"])
def test_busy_work_fails_without_partial_removal(records, monkeypatch, busy_namespace):
    actor, project, selected, _ = records
    message, operation = queue(selected, actor, PLAN_JOB_EVENTS[0])

    @contextmanager
    def lock(namespace, identifier):
        if namespace == busy_namespace:
            raise WorkAlreadyClaimed(str(identifier))
        yield

    monkeypatch.setattr("platform_api.floor_plan_removal.work_lock", lock)
    with pytest.raises(WorkAlreadyClaimed):
        set_floor_plan_removed(project, selected.pk, actor, removed=True)
    selected.refresh_from_db()
    message.refresh_from_db()
    operation.refresh_from_db()
    assert selected.review_state == "pending_page_evaluation"
    assert message.dead_lettered_at is None and operation.state == "pending"
    assert not AuditEvent.objects.exists()


def test_stale_confirmation_and_wrong_project_fail_closed(records, client):
    actor, project, selected, _ = records
    client.force_login(actor)
    old_version = selected.updated_at
    selected.plan_name = "Changed after confirmation"
    selected.save()
    with pytest.raises(ValueError, match="changed"):
        set_floor_plan_removed(project, selected.pk, actor, removed=True, expected_updated_at=old_version)
    other = Project.objects.create(organization=project.organization, submitted_name="Other")
    assert client.get(visibility_url(other, selected)).status_code == 404
    assert client.post(visibility_url(other, selected), {"confirm": "on"}).status_code == 404
    with pytest.raises(FloorPlan.DoesNotExist):
        set_floor_plan_removed(other, selected.pk, actor, removed=True)


@pytest.mark.parametrize("permissions", [[], ["view_project"], ["change_floorplan"]])
def test_permissions_required_on_get_post_and_service(records, client, django_user_model, permissions):
    _, project, selected, _ = records
    actor = django_user_model.objects.create_user(username="limited", is_staff=True)
    actor.user_permissions.add(*Permission.objects.filter(content_type__app_label="platform_api", codename__in=permissions))
    client.force_login(actor)
    assert client.get(visibility_url(project, selected)).status_code == 403
    assert client.post(visibility_url(project, selected), {"confirm": "on"}).status_code == 403
    with pytest.raises(PermissionDenied):
        set_floor_plan_removed(project, selected.pk, actor, removed=True)


def test_authorized_staff_csrf_and_http_method(records, django_user_model):
    _, project, selected, _ = records
    actor = django_user_model.objects.create_user(username="plan-editor", is_staff=True)
    actor.user_permissions.add(*Permission.objects.filter(content_type__app_label="platform_api",
                                                        codename__in=["change_floorplan", "view_project"]))
    client = Client(enforce_csrf_checks=True)
    client.force_login(actor)
    url = visibility_url(project, selected)
    response = client.get(url)
    assert response.status_code == 200
    assert client.post(url, {"confirm": "on"}).status_code == 403
    assert client.put(url, HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value).status_code == 405
    assert client.post(url, {"confirm": "on", "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        "expected_updated_at": response.context["form"].initial["expected_updated_at"]}).status_code == 302


def test_merged_project_restore_uses_canonical_scope(records, client):
    actor, source, selected, _ = records
    target = Project.objects.create(organization=source.organization, submitted_name="Canonical")
    ProjectMerge.objects.create(source_project=source, target_project=target, state="applied",
                                merged_by=actor, applied_at=timezone.now())
    client.force_login(actor)
    assert confirm(client, visibility_url(target, selected)).status_code == 302
    selected.refresh_from_db()
    link = FloorPlanAdmin(FloorPlan, admin.site).removal_action(selected)
    assert visibility_url(target, selected, "restore") in link
    assert confirm(client, visibility_url(target, selected, "restore")).status_code == 302


def test_removed_only_asset_hidden_but_not_deleted(records):
    actor, project, selected, retained = records
    # Unrelated plans with no asset must not poison NOT IN subqueries with NULL.
    FloorPlan.objects.create(project=project, document=selected.document, workflow_run=selected.workflow_run,
                             page_number=31, plan_name="No image", extractor_version="test")
    set_floor_plan_removed(project, selected.pk, actor, removed=True)
    assert visible_plan_assets(project.floor_plan_assets.all()).exists()
    set_floor_plan_removed(project, retained.pk, actor, removed=True)
    assert not visible_plan_assets(project.floor_plan_assets.all()).exists()
    assert storage.exists(selected.asset.object_key)


def test_api_and_published_facts_hide_removed_records(records):
    actor, project, selected, retained = records
    fact = ProjectFact.objects.create(project=project, field_code=f"floor_plan_{selected.pk.hex}",
        typed_value={"floorPlanId": str(selected.pk)}, source_class="human_approved", status="published",
        valid_from=timezone.now(), fact_set_version=project.published_fact_set_version)
    ProjectFact.objects.create(project=project, field_code="locality", typed_value={"value": "Thane"},
        source_class="human_approved", status="published", valid_from=timezone.now(),
        fact_set_version=project.published_fact_set_version)
    assert "facts" not in ProjectDetailSerializer(project).data
    project.status = "published"
    project.save(update_fields=["status"])
    assert len(ProjectDetailSerializer(project).data["facts"]) == 2
    set_floor_plan_removed(project, selected.pk, actor, removed=True)
    api = APIClient()
    api.force_authenticate(actor)
    response = api.get(f"/api/v1/developer-projects/{project.pk}/floor-plans")
    assert response.status_code == 200
    assert str(selected.pk) not in json.dumps(response.data)
    assert str(retained.pk) in json.dumps(response.data)
    assert api.post(f"/api/v1/review/floor-plans/{selected.pk}:resolve", {"decision": "approve"}).status_code == 404
    assert [row["code"] for row in ProjectDetailSerializer(project).data["facts"]] == ["locality"]
    detail = api.get(f"/api/v1/projects/{project.pk}/")
    assert detail.status_code == 200
    assert [row["code"] for row in detail.data["facts"]] == ["locality"]
    assert ProjectFact.objects.filter(pk=fact.pk).exists()
    set_floor_plan_removed(project, selected.pk, actor, removed=False)
    assert len(ProjectDetailSerializer(project).data["facts"]) == 2
    restored = api.get(f"/api/v1/projects/{project.pk}/")
    assert restored.status_code == 200 and len(restored.data["facts"]) == 2


@pytest.mark.parametrize("output_name", ["layout-metrics", "layout-metrics-csv"])
def test_historical_downloads_filter_without_rewriting_artifact(records, output_name):
    actor, project, selected, retained = records
    run = selected.workflow_run.ingestion_run
    output = build_project_output(run, [], [])
    run.summary = {"outputs": store_outputs(run, output, [selected, retained])}
    run.save()
    artifact = run.summary["outputs"][output_name]
    with storage.open(artifact["objectKey"], "rb") as source:
        original_bytes = source.read()
    set_floor_plan_removed(project, selected.pk, actor, removed=True)
    api = APIClient()
    api.force_authenticate(actor)
    response = api.get(artifact["uri"])
    assert response.status_code == 200
    content = b"".join(response.streaming_content)
    response.close()
    rows = json.loads(content)["layouts"] if output_name == "layout-metrics" else list(
        csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    assert [row["floorPlanId"] for row in rows] == [str(retained.pk)]
    with storage.open(artifact["objectKey"], "rb") as source:
        assert source.read() == original_bytes
    selected.refresh_from_db()
    new_outputs = store_outputs(run, output, [selected, retained])
    with storage.open(new_outputs["layout-metrics"]["objectKey"], "rb") as source:
        assert [row["floorPlanId"] for row in json.load(source)["layouts"]] == [str(retained.pk)]