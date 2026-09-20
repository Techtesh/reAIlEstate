import json
from decimal import Decimal
from unittest.mock import Mock

import pytest

from platform_api import builder_views
from platform_api.builder_locations import BUILDER_LOCATION_SOURCE
from platform_api.external_sources_admin import apply_manual_reference
from platform_api.models import AuditEvent, Project, ReviewTask
from platform_api.project_map_locations import project_map_location
from platform_api.tests.test_builder_portal import project_url, workspace  # noqa: F401


pytestmark = pytest.mark.django_db
POINT = {"latitude": 19.123456, "longitude": 72.987654}


def save_location(client, project, location, **values):
    return client.patch(project_url(project), {
        "location": location, "expectedUpdatedAt": project.updated_at.isoformat(), **values,
    }, format="json")


@pytest.mark.parametrize("location", [
    POINT,
    {"mapsUrl": "https://www.google.com/maps/search/?api=1&query=19.123456,72.987654"},
    {"mapsUrl": "https://www.google.com/maps/place/Project/data=!3d19.123456!4d72.987654"},
])
def test_location_saved_as_private_review_proposal_not_project_fact(workspace, settings, location):
    client, user, _, project, _ = workspace
    project.status = "published"
    project.save()
    previous_version = project.updated_at
    response = save_location(client, project, location)
    assert response.status_code == 200
    assert response.json()["location"] == {**POINT, "reviewState": "unreviewed"}
    assert response["Cache-Control"] == "private, no-store"
    project.refresh_from_db()
    assert project.updated_at != previous_version and project.status == "needs_review"
    assert project.latitude is None and project.longitude is None
    task = project.review_tasks.get(task_type="external_sources")
    assert task.resolution["manualLocation"]["location"] == POINT
    assert task.resolution["submittedBy"] == user.pk and task.state == "open"
    assert task.resolution["source"] == BUILDER_LOCATION_SOURCE
    assert project.review_tasks.filter(task_type="publication", state="open").exists()
    for preview in (False, True):
        settings.PROJECT_PUBLIC_PREVIEW_ENABLED = preview
        assert project_map_location(project)["latitude"] is None
    detail = client.get(project_url(project)).json()
    listing = client.get("/api/v1/builder/projects").json()[0]
    assert detail["location"] == listing["location"] == response.json()["location"]
    metadata = json.dumps(list(AuditEvent.objects.filter(entity_id=project.pk).values_list("metadata", flat=True)))
    assert "19.123456" not in metadata and "72.987654" not in metadata and "https://" not in metadata
    assert save_location(client, project, location).status_code == 200
    project.refresh_from_db()
    assert project.review_tasks.filter(task_type="external_sources").count() == 1
    assert project.updated_at.isoformat().replace("+00:00", "Z") == response.json()["updatedAt"]


def test_create_project_with_location_and_optional_omission(workspace):
    client, _, member, _, _ = workspace
    body = {
        "organizationId": str(member.organization_id), "submittedName": "Located project",
        "mahaReraId": "P51800000003", "locality": "Powai", "location": POINT,
    }
    response = client.post("/api/v1/builder/projects", body, format="json")
    assert response.status_code == 201
    project = Project.objects.get(pk=response.json()["id"])
    assert project.latitude is None and project.status != "published"
    assert response.json()["location"] == {**POINT, "reviewState": "unreviewed"}
    assert client.patch(project_url(project), {
        "submittedName": "New name", "expectedUpdatedAt": project.updated_at.isoformat(),
    }, format="json").json()["location"] == response.json()["location"]
    body.pop("location")
    body["mahaReraId"] = "P51800000004"
    assert client.post("/api/v1/builder/projects", body, format="json").json()["location"] is None


@pytest.mark.parametrize("location", [
    {}, None, {"latitude": 19}, {"longitude": 72}, {"latitude": None, "longitude": None},
    {"latitude": True, "longitude": 72}, {"latitude": "19", "longitude": 72},
    {"latitude": 91, "longitude": 72}, {"latitude": 19, "longitude": -181},
    {**POINT, "mapsUrl": "https://maps.app.goo.gl/test"}, {**POINT, "reviewState": "accepted"},
    {"mapsUrl": ""}, {"mapsUrl": "https://127.0.0.1/maps?q=19,72"},
    {"mapsUrl": "https://google.com.evil.test/maps?q=19,72"},
    {"mapsUrl": "https://user:password@www.google.com/maps?q=19,72"},
    {"mapsUrl": "http://www.google.com/maps?q=19,72"},
    {"mapsUrl": "https://www.google.com:8443/maps?q=19,72"},
    {"mapsUrl": "https://www.google.com/maps/@19,72,15z"},
    {"mapsUrl": "https://www.google.com/maps/dir/?q=19,72"},
    {"mapsUrl": "https://www.google.com/maps?q=19,72&query=20,73"},
])
def test_invalid_location_is_atomic_and_does_not_echo_private_input(workspace, location):
    client, _, _, project, _ = workspace
    version = project.updated_at
    response = save_location(client, project, location, submittedName="Must not change")
    assert response.status_code == 400
    project.refresh_from_db()
    assert project.updated_at == version and project.submitted_name == "My project"
    assert not project.review_tasks.exists()
    assert "127.0.0.1" not in response.content.decode() and "user:password" not in response.content.decode()


@pytest.mark.parametrize("guard", ["foreign", "stale", "archived", "revoked", "missing_version"])
def test_location_resolution_only_after_object_access_and_version_check(workspace, monkeypatch, guard):
    client, _, membership, project, foreign = workspace
    resolve = Mock(side_effect=AssertionError("Must not resolve an unauthorized or stale request"))
    monkeypatch.setattr(builder_views, "resolve_project_location", resolve)
    version = project.updated_at.isoformat()
    if guard == "foreign":
        project = foreign
        version = project.updated_at.isoformat()
    elif guard == "stale":
        project.save()
    elif guard == "archived":
        project.status = "retired"
        project.save()
        version = project.updated_at.isoformat()
    elif guard == "revoked":
        membership.status = "revoked"
        membership.save()
    payload = {"location": {"mapsUrl": "https://maps.app.goo.gl/test"}}
    if guard != "missing_version":
        payload["expectedUpdatedAt"] = version
    response = client.patch(project_url(project), payload, format="json")
    assert response.status_code == (404 if guard in {"foreign", "revoked"} else 400 if guard == "missing_version" else 409)
    resolve.assert_not_called()


def test_short_links_reuse_existing_resolver_and_fail_without_mutation(workspace, monkeypatch):
    from platform_api.management.commands import import_google_project_place_decisions as maps

    client, _, _, project, _ = workspace
    resolve = Mock(return_value={"name": "Project", "location": POINT})
    monkeypatch.setattr(maps, "resolve_share_url", resolve)
    short = "https://maps.app.goo.gl/project"
    response = save_location(client, project, {"mapsUrl": short})
    assert response.status_code == 200
    resolve.assert_called_once_with(short)
    task = project.review_tasks.get(task_type="external_sources")
    assert task.resolution["manualLocation"]["evidenceMethod"] == "manual_maps_link"
    assert short not in json.dumps(task.resolution)
    project.refresh_from_db()
    version = project.updated_at
    resolve.side_effect = OSError("provider details must not leak")
    response = save_location(client, project, {"mapsUrl": short})
    assert response.status_code == 400 and b"provider details" not in response.content
    project.refresh_from_db()
    assert project.updated_at == version


def test_builder_proposal_requires_independent_reviewer_and_supersedes_old_proposals(workspace, django_user_model, settings):
    client, user, _, project, _ = workspace
    assert save_location(client, project, POINT).status_code == 200
    first = project.review_tasks.get(task_type="external_sources")
    project.refresh_from_db()
    assert save_location(client, project, {"latitude": 0, "longitude": 0}).status_code == 200
    project.refresh_from_db()
    first.refresh_from_db()
    assert first.state == "resolved" and first.resolution["reviewState"] == "superseded"
    second = project.review_tasks.get(task_type="external_sources", state="open")
    user.is_staff = user.is_superuser = True
    user.save()
    settings.PROJECT_LOCATION_REQUIRE_SECOND_APPROVER = False
    with pytest.raises(ValueError, match="different reviewer"):
        apply_manual_reference(project, user, second.pk, project.updated_at, second.version)
    reviewer = django_user_model.objects.create_superuser(username="independent-location-reviewer")
    with pytest.raises(ValueError, match="open"):
        apply_manual_reference(project, reviewer, first.pk, project.updated_at, first.version)
    apply_manual_reference(project, reviewer, second.pk, project.updated_at, second.version)
    project.refresh_from_db()
    second.refresh_from_db()
    assert project.latitude == Decimal("0") and project.longitude == Decimal("0")
    assert second.resolution["approvalPolicy"] == {"secondApproverRequired": True, "selfApproved": False}
    assert project.status != "published"
    assert client.get(project_url(project)).json()["location"]["reviewState"] == "accepted"
