from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from platform_api.external_sources import GoogleProjectMaps, coordinates, registry_lookup, station_routes
from platform_api.external_sources_admin import RegistryEvidenceForm, commit_references, draft_key
from platform_api.models import AuditEvent, Organization, Project, ReviewTask, VerificationSnapshot


def test_invalid_coordinates_never_reach_routes():
    for latitude in (True, float("nan"), float("inf"), 91, "19.1"):
        with pytest.raises(ValueError):
            coordinates({"latitude": latitude, "longitude": 72.9})


def test_no_google_key_fails_closed(settings):
    settings.GOOGLE_MAPS_API_KEY = ""
    with pytest.raises(ValueError, match="not configured"):
        GoogleProjectMaps().search("Example project, Thane, Maharashtra")


def test_google_route_distinguishes_no_route_and_failure(monkeypatch):
    maps = GoogleProjectMaps()
    origin = {"latitude": 19.2, "longitude": 72.9}
    request = Mock(return_value={"routes": []})
    monkeypatch.setattr(maps, "_request", request)
    assert maps.route(origin, origin, "WALK")["status"] == "no_route"
    request.side_effect = ValueError("Provider down")
    assert maps.route(origin, origin, "WALK")["status"] == "unavailable"
    request.side_effect = None
    request.return_value = {"routes": [{"distanceMeters": 800, "duration": "600s"}]}
    result = maps.route(origin, origin, "DRIVE")
    assert result["distanceMeters"] == 800 and result["minutes"] == 10
    assert "travelmode=driving" in result["directionsUrl"]


def test_route_ranking_and_authority_status_override(settings):
    origin = {"latitude": 19.2, "longitude": 72.9}
    settings.PROJECT_METRO_SOURCE_HOSTS = ("mmrda.maharashtra.gov.in",)
    settings.PROJECT_METRO_CATALOGUE = [{
        "name": "Future station", "location": origin, "googlePlaceId": "planned-id", "line": "Test line",
        "serviceStatus": "under_construction", "sourceUrl": "https://mmrda.maharashtra.gov.in/",
        "asOf": "2026-01-01",
    }]
    first = {"name": "A", "kind": "railway", "serviceStatus": "existing_candidate", "location": {"latitude": 19.21, "longitude": 72.9}}
    second = {"name": "B", "kind": "railway", "serviceStatus": "existing_candidate", "location": {"latitude": 19.22, "longitude": 72.9}}
    planned = {"name": "Future station", "placeId": "planned-id", "kind": "metro", "serviceStatus": "existing_candidate", "location": origin}
    maps = Mock()
    maps.nearby.side_effect = lambda location, kind, radius: [first, second] if kind == "railway" else [planned]
    maps.route.side_effect = lambda start, end, mode: {"status": "available", "distanceMeters": 2000 if end["latitude"] == 19.21 else 500, "minutes": 5}
    result = station_routes({"location": origin}, 10000, maps=maps)
    assert result["groups"][0]["nearest"]["walking"] == "B"
    assert result["groups"][1]["stations"] == []
    assert result["groups"][2]["stations"][0]["serviceStatus"] == "under_construction"
    assert result["isNearestVerified"] is False
    assert maps.route.call_count == 6


def test_registry_exact_match_and_manual_provenance(monkeypatch):
    provider = Mock()
    provider.registration.return_value = {"sourceUrl": "https://maharera.maharashtra.gov.in/project",
                                          "retrievedAt": "2026-01-01T00:00:00Z", "data": {"registrationNumber": "P51700099999"}}
    monkeypatch.setattr("platform_api.external_sources.backend", lambda: provider)
    assert registry_lookup("P51700053528")["reasonCode"] == "REGISTRATION_MISMATCH"
    form = RegistryEvidenceForm({"registration_number": "P51700053528", "source_url": "https://example.com/",
                                 "checked_on": "2026-01-01", "evidence": "Project details"})
    assert not form.is_valid() and "source_url" in form.errors


def test_draft_key_is_owner_and_project_bound():
    token = str(uuid4())
    project, other = SimpleNamespace(pk=uuid4()), SimpleNamespace(pk=uuid4())
    owner, stranger = SimpleNamespace(pk=1), SimpleNamespace(pk=2)
    assert draft_key(project, owner, token) != draft_key(project, stranger, token)
    assert draft_key(project, owner, token) != draft_key(other, owner, token)


@pytest.mark.django_db
def test_commit_stores_references_not_google_content_or_verified_facts(django_user_model):
    actor = django_user_model.objects.create_superuser(username="sources-reviewer", password="test-only")
    project = Project.objects.create(organization=Organization.objects.create(legal_name="External test"), submitted_name="Test project")
    before = Project.objects.values().get(pk=project.pk)
    draft = {"projectVersion": project.updated_at.isoformat(), "selected": {
        "placeId": "google-place-id", "name": "Provider content", "location": {"latitude": 19.2, "longitude": 72.9}},
        "routes": {"distanceMeters": 900}}
    token = str(uuid4())
    task = commit_references(project, actor, draft, token)
    assert task.resolution["googlePlaceId"] == "google-place-id"
    assert "location" not in task.resolution and "routes" not in task.resolution
    assert "Provider content" not in str(task.resolution)
    assert not VerificationSnapshot.objects.exists()
    assert Project.objects.values().get(pk=project.pk) == before
    assert commit_references(project, actor, draft, token).pk == task.pk
    assert ReviewTask.objects.count() == 1 and AuditEvent.objects.count() == 1
    project.locality = "Changed"
    project.save()
    with pytest.raises(ValueError, match="Project changed"):
        commit_references(project, actor, draft, str(uuid4()))


@pytest.mark.django_db
def test_nonstaff_cannot_commit(django_user_model):
    actor = django_user_model.objects.create_user(username="external-reader")
    with pytest.raises(PermissionDenied):
        commit_references(None, actor, {}, str(uuid4()))