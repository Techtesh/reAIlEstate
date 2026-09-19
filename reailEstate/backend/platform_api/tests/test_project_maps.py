import pytest
from rest_framework.test import APIClient

from platform_api.map_catalogue import map_stations
from platform_api.models import Organization, Project, ReviewTask, StationCandidate
from platform_api.project_map_locations import project_map_location


pytestmark = pytest.mark.django_db


@pytest.fixture
def project():
    return Project.objects.create(
        organization=Organization.objects.create(legal_name="Map fixtures"),
        submitted_name="Saved location fixture", status="published",
    )


def reference(project, *, google=False, review_state="unreviewed"):
    location = {"latitude": 19.2, "longitude": 72.95}
    resolution = {"reviewState": review_state}
    if google:
        resolution.update(googlePlaceId="test-place", stationRoutes={
            "originPlaceId": "test-place", "origin": location,
        })
    else:
        resolution["manualLocation"] = {"location": location}
    return ReviewTask.objects.create(project=project, task_type="external_sources", resolution=resolution)


@pytest.mark.parametrize("preview", [False, True])
@pytest.mark.parametrize("google", [False, True])
@pytest.mark.parametrize("status", ["published", "ingestion", "needs_review"])
def test_saved_locations_are_explicitly_provisional_only_in_preview(project, settings, preview, google, status):
    settings.PROJECT_PUBLIC_PREVIEW_ENABLED = preview
    project.status = status
    project.save()
    task = reference(project, google=google)
    result = APIClient().get("/api/v1/projects/?includeProcessing=true").json()["results"][0]
    assert result["locationStatus"] == ("provisional" if preview else "unavailable")
    assert (float(result["latitude"]) if preview else result["latitude"]) == (19.2 if preview else None)
    assert (float(result["longitude"]) if preview else result["longitude"]) == (72.95 if preview else None)
    assert "test-place" not in str(result)
    project.refresh_from_db()
    task.refresh_from_db()
    assert project.latitude is None and project.longitude is None
    assert task.state == "open" and task.resolution["reviewState"] == "unreviewed"


def test_preview_never_uses_mismatched_routes_or_rejected_references(project, settings):
    settings.PROJECT_PUBLIC_PREVIEW_ENABLED = True
    task = reference(project, google=True)
    task.resolution["stationRoutes"]["originPlaceId"] = "a-different-place"
    task.save()
    assert project_map_location(project)["location_status"] == "unavailable"
    reference(project, review_state="rejected")
    assert project_map_location(project)["latitude"] is None
    reference(project)
    assert project_map_location(project)["location_status"] == "provisional"
    for status in ("draft", "retired", "merged"):
        project.status = status
        assert project_map_location(project)["latitude"] is None


def test_published_coordinates_take_precedence_and_processing_requires_acceptance(project, settings):
    settings.PROJECT_PUBLIC_PREVIEW_ENABLED = False
    project.latitude, project.longitude = 19.1, 72.9
    reference(project)
    assert project_map_location(project) == {"latitude": 19.1, "longitude": 72.9, "location_status": "published"}
    project.status = "needs_review"
    assert project_map_location(project)["latitude"] is None
    task = reference(project, review_state="accepted")
    task.state = "resolved"
    task.save()
    assert project_map_location(project)["location_status"] == "reviewed"


@pytest.mark.parametrize("location", [None, {}, {"latitude": 91, "longitude": 72}, {"latitude": True, "longitude": 72}])
def test_invalid_saved_coordinates_do_not_invent_pins(project, settings, location):
    settings.PROJECT_PUBLIC_PREVIEW_ENABLED = True
    task = reference(project)
    task.resolution["manualLocation"]["location"] = location
    task.save()
    assert project_map_location(project)["latitude"] is None


@pytest.fixture
def stations(settings, monkeypatch):
    settings.PROJECT_MAP_STATION_CANDIDATES_FILE = ""
    monkeypatch.setattr("platform_api.map_catalogue.metro_catalogue", lambda: [])
    rows = [
        ("Miragaon", "Line 9"), ("Intermediate", "Line 9"), ("Dahisar East", "Line 9"),
        ("Dahisar East", "Line 7"), ("Gundavali", "Line 7"), ("Airport Colony", "Line 7A"),
        ("Kasarvadavali", "Line 4"), ("Gowniwada", "Line 4A"),
    ]
    return [StationCandidate.objects.create(
        name=name, line=line, latitude=19 + index / 100, longitude=72.9,
        sequence=index, needs_review=False,
    ) for index, (name, line) in enumerate(rows)]


def test_requested_connections_use_correct_lines_and_schematic_provenance(stations):
    result = map_stations(include_saved=False)
    by_id = {station["id"]: station for station in result["stations"]}
    assert [
        [(by_id[key]["name"], by_id[key]["line"]) for key in connection["stationIds"]]
        for connection in result["connections"]
    ] == [
        [("Miragaon", "Line 9"), ("Dahisar East", "Line 9")],
        [("Gundavali", "Line 7"), ("Airport Colony", "Line 7A")],
        [("Kasarvadavali", "Line 4"), ("Gowniwada", "Line 4A")],
    ]
    assert all(item["source"] == "user_requested" and item["geometryStatus"] == "schematic_unverified"
               for item in result["connections"])
    assert map_stations(include_saved=False) == result


def test_connections_do_not_duplicate_existing_edges_or_use_withheld_stations(stations):
    StationCandidate.objects.filter(name="Intermediate").delete()
    StationCandidate.objects.filter(name="Airport Colony").update(active=False)
    StationCandidate.objects.filter(name="Gowniwada").update(needs_review=True)
    assert map_stations(include_saved=False)["connections"] == []


def test_ambiguous_aliases_do_not_create_arbitrary_connections(stations):
    StationCandidate.objects.create(name="Kasarwadavli", line="Line 4", latitude=19.5,
                                   longitude=73, needs_review=False)
    assert len(map_stations(include_saved=False)["connections"]) == 2
