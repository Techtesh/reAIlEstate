"""Floor observations must never be corrected through the individual-flat form."""
from copy import deepcopy
from io import BytesIO

import pytest
from django.contrib.auth.models import Permission
from rest_framework.test import APIClient

from platform_api.models import FloorPlanAsset, OutboxMessage, ProjectFact, ReviewTask
from .test_floor_plan_layout_jobs import source

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def floor_review(source, settings):
    settings.ROOT_URLCONF = "admin_service.urls"
    settings.SECURE_SSL_REDIRECT = False
    settings.ALLOWED_HOSTS = ["testserver"]
    plan, actor = source
    plan.plan_scope = "floor"
    plan.features = {"keep": "source metadata", "floorEvaluation": {
        "flatsPerFloor": 8, "liftCount": 2, "staircaseCount": 1,
        "assetSha256": plan.asset.sha256, "evaluatedAt": "2026-01-01T00:00:00Z",
    }}
    plan.save()
    client = APIClient()
    client.force_login(actor)
    body = {"scope": "floor", "decision": "save-correction", "reason": "Compared source labels",
        "expectedUpdatedAt": plan.updated_at.isoformat(), "floorEvaluation": {
            "flatsPerFloor": 10, "liftCount": 0, "staircaseCount": None,
            "refugeArea": {"presence": "present", "area": 40.5, "unit": "sqm", "evidence": "Printed refuge label"},
            "commonAreas": [], "visualEvidence": "Ten labelled flat entrances",
        }}
    return client, plan, body, f"/api/v1/admin/plans/{plan.pk}/review"


def test_floor_correction_preserves_layout_fields_and_provenance(floor_review):
    client, plan, body, url = floor_review
    previous = (plan.dimensions, plan.configuration_type, plan.carpet_area_sqft)
    assert client.post(url, body, format="json").status_code == 200
    plan.refresh_from_db()
    summary = plan.features["floorEvaluation"]
    assert summary["flatsPerFloor"] == 10 and summary["liftCount"] == 0 and summary["staircaseCount"] is None
    assert summary["evaluatedAt"] == "2026-01-01T00:00:00Z"
    assert plan.features["keep"] == "source metadata"
    assert (plan.dimensions, plan.configuration_type, plan.carpet_area_sqft) == previous
    assert plan.review_state == "needs_review" and not ProjectFact.objects.exists()
    task = ReviewTask.objects.get(resolution__source="manual_floor_evaluation")
    assert task.resolution["before"]["flatsPerFloor"] == 8
    assert client.post(url, body, format="json").status_code == 409
    result = client.post(url, {**body, "expectedUpdatedAt": plan.updated_at.isoformat(), "decision": "approve"}, format="json")
    assert result.status_code == 200, result.content
    task.refresh_from_db()
    assert task.state == "resolved" and task.version == 2
    assert task.resolution["before"]["flatsPerFloor"] == 8
    assert not ProjectFact.objects.exists()


@pytest.mark.parametrize("change", ["layout-fields", "bad-count", "unknown-area", "wrong-scope", "pending", "stale-source"])
def test_floor_review_rejects_invalid_or_busy_sources(floor_review, change):
    client, plan, body, url = floor_review
    body = deepcopy(body)
    expected = 400
    if change == "layout-fields": body["fields"] = {"room_count": 5}
    elif change == "bad-count": body["floorEvaluation"]["flatsPerFloor"] = -1
    elif change == "unknown-area": body["floorEvaluation"]["refugeArea"]["presence"] = "unknown"
    elif change == "wrong-scope": body["scope"] = "flat"
    elif change == "pending":
        OutboxMessage.objects.create(event_type="floor_plan.units_evaluation.requested", payload={"floorPlanId": str(plan.pk)})
        expected = 409
    else:
        plan.features["floorEvaluation"]["assetSha256"] = "0" * 64
        plan.save()
        body["expectedUpdatedAt"] = plan.updated_at.isoformat()
        expected = 409
    result = client.post(url, body, format="json")
    assert result.status_code == expected, result.content
    plan.refresh_from_db()
    assert plan.features["floorEvaluation"]["flatsPerFloor"] == 8
    assert not ReviewTask.objects.filter(resolution__source="manual_floor_evaluation").exists()


def test_floor_correction_requires_another_reviewer(floor_review, django_user_model):
    client, plan, body, url = floor_review
    editor = django_user_model.objects.create_user(username="floor-editor", is_staff=True)
    editor.user_permissions.add(*Permission.objects.filter(content_type__app_label="platform_api",
        codename__in=["view_project", "change_floorplan"]))
    client.force_login(editor)
    assert client.post(url, body, format="json").status_code == 200
    plan.refresh_from_db()
    assert client.post(url, {**body, "expectedUpdatedAt": plan.updated_at.isoformat(), "decision": "approve"}, format="json").status_code == 403


@pytest.mark.parametrize("target", ["document", "asset"])
def test_floor_review_verifies_source_bytes_before_writing(floor_review, monkeypatch, target):
    from django.db import connection
    from platform_api.artifact_storage import storage

    client, plan, body, url = floor_review
    key = plan.document.object_key if target == "document" else plan.asset.object_key
    original_open = storage.open

    def open_source(name, *args, **kwargs):
        assert not connection.in_atomic_block
        return BytesIO(b"tampered source") if name == key else original_open(name, *args, **kwargs)

    monkeypatch.setattr(storage, "open", open_source)
    result = client.post(url, body, format="json")
    assert result.status_code == 400, result.content
    plan.refresh_from_db()
    assert plan.features["floorEvaluation"]["flatsPerFloor"] == 8
    assert not ReviewTask.objects.filter(resolution__source="manual_floor_evaluation").exists()


def test_floor_review_rechecks_asset_version_after_reading_bytes(floor_review, monkeypatch):
    from platform_api import floor_evaluation_review as review

    client, plan, body, url = floor_review
    original_read = review._read_asset

    def replace_asset(asset):
        content = original_read(asset)
        FloorPlanAsset.objects.filter(pk=asset.pk).update(sha256="f" * 64)
        return content

    monkeypatch.setattr(review, "_read_asset", replace_asset)
    result = client.post(url, body, format="json")
    assert result.status_code == 409, result.content
    assert not ReviewTask.objects.filter(resolution__source="manual_floor_evaluation").exists()


def test_floor_correction_clears_approval_metadata_and_preserves_history(floor_review):
    client, plan, body, url = floor_review
    plan.features["floorEvaluation"].update(reviewedBy=123, reviewedAt="2026-01-02", reviewReason="Old approval")
    plan.save()
    body["expectedUpdatedAt"] = plan.updated_at.isoformat()
    assert client.post(url, body, format="json").status_code == 200
    plan.refresh_from_db()
    summary = plan.features["floorEvaluation"]
    assert summary["reviewState"] == "unreviewed"
    assert not {"reviewedBy", "reviewedAt", "reviewReason"} & summary.keys()
    assert ReviewTask.objects.get(resolution__source="manual_floor_evaluation").resolution["before"]["reviewedBy"] == 123


def test_flat_review_rejects_floor_observations(floor_review):
    client, plan, body, url = floor_review
    plan.plan_scope = "flat"
    plan.save()
    body["expectedUpdatedAt"] = plan.updated_at.isoformat()
    assert client.post(url, body, format="json").status_code == 400
    assert not ProjectFact.objects.exists()
