from types import SimpleNamespace
import json
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from platform_api.external_sources import GoogleProjectMaps, coordinates, registry_lookup, station_routes
from platform_api.external_sources_admin import RegistryEvidenceForm, commit_references, draft_key
from platform_api.models import AuditEvent, Organization, Project, ReviewTask, VerificationSnapshot


@pytest.mark.parametrize("origin_id,destination_id,expected", [
    ("building-id", "station-id", "places"), (None, "station-id", "coordinates"),
    ("building-id", None, "coordinates"), (None, None, "coordinates"),
    ("https://maps.google.com/search", "station-id", "coordinates"),
])
def test_route_endpoint_options(monkeypatch, origin_id, destination_id, expected):
    from urllib.parse import parse_qs, urlsplit

    maps = GoogleProjectMaps()
    request = Mock(return_value={"routes": [{"distanceMeters": 1200, "duration": "900s"}]})
    monkeypatch.setattr(maps, "_request", request)
    origin = {"latitude": 19.1, "longitude": 72.9, "placeId": origin_id}
    destination = {"latitude": 19.2, "longitude": 73.0, "placeId": destination_id}
    result = maps.route(origin, destination, "WALK")
    assert result["endpointMode"] == expected and result["status"] == "available"
    body = request.call_args.args[2]
    assert body["travelMode"] == "WALK"
    query = parse_qs(urlsplit(result["directionsUrl"]).query)
    if expected == "places":
        assert body["origin"] == {"placeId": origin_id}
        assert body["destination"] == {"placeId": destination_id}
        assert query["origin_place_id"] == [origin_id] and query["destination_place_id"] == [destination_id]
    else:
        assert body["origin"] == {"location": {"latLng": {"latitude": 19.1, "longitude": 72.9}}}
        assert body["destination"] == {"location": {"latLng": {"latitude": 19.2, "longitude": 73.0}}}
        assert "origin_place_id" not in query and "destination_place_id" not in query


def test_station_routes_include_csv_candidates_and_endpoint_modes(settings, tmp_path):
    path = tmp_path / "stations.csv"
    path.write_text("name,line,latitude,longitude\nKasarvadavali,Line 4,19.285,72.9615\n"
                    "Kasarvadavali,Line 4,19.285,72.9615\nConflict,Line 4,19.28,72.96\n"
                    "Conflict,Line 4,19.29,72.95\nFar away,Line 1,18,72\n", encoding="utf-8")
    settings.PROJECT_MAP_STATION_CANDIDATES_FILE = str(path)
    settings.PROJECT_METRO_CATALOGUE = []
    maps = GoogleProjectMaps()
    maps.nearby = Mock(side_effect=lambda origin, kind, radius: [
        {"name": "Known station", "kind": kind, "serviceStatus": "existing_candidate",
         "placeId": "station-id", "location": {"latitude": 19.27, "longitude": 72.98}}
    ] if kind == "railway" else [])
    maps._request = Mock(return_value={"routes": [{"distanceMeters": 1200, "duration": "900s"}]})
    result = station_routes({"location": {"latitude": 19.2701641, "longitude": 72.9726405},
                             "placeId": "building-id"}, 10000, maps)
    assert result["candidateCount"] == 1
    railway = result["groups"][0]["stations"][0]
    candidate = result["groups"][3]["stations"][0]
    assert railway["walking"]["endpointMode"] == "places"
    assert candidate["name"] == "Kasarvadavali"
    assert candidate["serviceStatus"] == "unverified"
    assert candidate["walking"]["endpointMode"] == "coordinates"
    assert result["groups"][2]["stations"] == []
    assert result["errors"] == []


@pytest.mark.django_db
def test_database_station_import_and_editing(settings, tmp_path, client, django_user_model):
    from decimal import Decimal
    from django.core.management import call_command
    from django.urls import reverse
    from platform_api.map_catalogue import map_stations
    from platform_api.models import StationCandidate

    settings.PROJECT_MAP_STATION_CANDIDATES_FILE = ""
    settings.PROJECT_METRO_CATALOGUE = []
    path = tmp_path / "stations.csv"
    path.write_text("name,line,latitude,longitude\nOne,Line 4,19.1,72.9\nOne,Line 4,19.1,72.9\n"
                    "Conflict,Line 4,19.2,72.9\nTwo,Line 4,19.3,72.9\n"
                    "Conflict,Line 4,19.4,72.9\nThree,Line 4,19.5,72.9\n", encoding="utf-8")
    call_command("import_station_candidates", str(path))
    assert StationCandidate.objects.count() == 4
    conflict = StationCandidate.objects.get(name="Conflict")
    assert conflict.needs_review and conflict.latitude is None and len(conflict.coordinate_options) == 2
    catalogue = map_stations(include_saved=False)
    assert {row["name"] for row in catalogue["stations"]} == {"One", "Two", "Three"}
    assert len(catalogue["lines"]) == 1
    conflict.latitude, conflict.longitude, conflict.needs_review = Decimal("19.25"), Decimal("72.95"), False
    conflict.full_clean()
    conflict.save()
    call_command("import_station_candidates", str(path))
    conflict.refresh_from_db()
    assert conflict.latitude == Decimal("19.25") and not conflict.needs_review
    catalogue = map_stations(include_saved=False)
    assert len(catalogue["stations"]) == 4
    assert len(catalogue["lines"][0]["stationIds"]) == 4
    assert all(row["serviceStatus"] == "unverified" for row in catalogue["stations"])
    assert next(row for row in catalogue["stations"] if row["name"] == "Conflict")["location"]["latitude"] == 19.25
    published = Project.objects.create(organization=Organization.objects.create(legal_name="Station snapshots"),
        submitted_name="Published", status="published")
    ReviewTask.objects.create(project=published, task_type="external_sources", state="resolved", resolution={
        "reviewState": "accepted", "stationRoutes": {"groups": [{"stations": [{
            "name": "Old coordinates", "kind": "metro", "line": "Line 4", "source": "user_candidate",
            "location": {"latitude": 19.0, "longitude": 72.0}}]}]}})
    assert "Old coordinates" not in {row["name"] for row in map_stations()["stations"]}
    actor = django_user_model.objects.create_superuser(username="station-editor", password="test-only")
    client.force_login(actor)
    assert client.get(reverse("admin:platform_api_stationcandidate_changelist"), {"needs_review__exact": "1"}).status_code == 200


@pytest.mark.django_db
def test_station_choices_save_render_and_reject_stale(client, django_user_model):
    from django.urls import reverse
    from platform_api.external_sources_admin import (
        project_transit_maps, route_revision, save_transit_station_choices, station_choice_key,
    )

    actor = django_user_model.objects.create_superuser(username="station-selector", password="test-only")
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Choices"),
        submitted_name="Choice project", latitude=19.1, longitude=72.9)
    stations = [{"name": name, "kind": kind, "serviceStatus": status,
        "location": {"latitude": 19.2, "longitude": 72.95},
        "walking": {"status": "available", "distanceMeters": distance}} for name, kind, status, distance in (
            ("Existing", "metro", "existing_candidate", 100), ("Upcoming", "metro", "planned", 300),
            ("Unverified", "metro", "unverified", 200), ("Rail", "railway", "existing_candidate", 500))]
    routes = {"groups": [{"label": station["name"], "stations": [station]} for station in stations], "errors": [
        "Some station catalogue candidates are unavailable or invalid. Review the candidate CSV."]}
    task = ReviewTask.objects.create(project=project, task_type="external_sources", resolution={
        "googlePlaceId": "building", "stationRoutes": routes})
    client.force_login(actor)
    url = reverse("admin:platform_api_project_external_sources", args=[project.pk])
    form = client.get(url).context["station_choice_form"]
    assert len(form.fields["metro"].choices) == 4 and len(form.fields["railway"].choices) == 2
    data = {"action": "save_station_choices", "project_version": project.updated_at.isoformat(),
        "route_revision": route_revision(routes), "metro": station_choice_key(stations[1]),
        "railway": station_choice_key(stations[3])}
    assert client.post(url, data).status_code == 302
    project.refresh_from_db()
    assert project_transit_maps(project)["views"][0]["station"] == "Upcoming"
    assert project_transit_maps(project)["views"][0]["selected"]
    assert project_transit_maps(project)["views"][0]["errors"] == []
    assert AuditEvent.objects.filter(action="project.transit_stations_selected", entity_id=project.pk).count() == 1
    with pytest.raises(ValueError, match="changed"):
        save_transit_station_choices(project, actor, data)
    data["project_version"] = project.updated_at.isoformat()
    with pytest.raises(ValueError, match="Choose stations"):
        save_transit_station_choices(project, actor, data | {"metro": "foreign-station"})
    outsider = django_user_model.objects.create_user(username="unauthorized-stations", is_staff=True)
    with pytest.raises(PermissionDenied):
        save_transit_station_choices(project, outsider, data)
    task.resolution["stationRoutes"]["groups"][1]["stations"][0]["walking"] = {"status": "unavailable"}
    task.save()
    assert project_transit_maps(project)["views"][0]["distance_meters"] is None
    with pytest.raises(ValueError, match="changed"):
        save_transit_station_choices(project, actor, data)
    data["route_revision"] = route_revision(task.resolution["stationRoutes"])
    save_transit_station_choices(project, actor, data | {"metro": "", "railway": ""})
    project.refresh_from_db()
    assert project_transit_maps(project)["views"][0]["station"] == "Existing"
    project.status = "retired"
    project.save()
    with pytest.raises(ValueError, match="active canonical"):
        save_transit_station_choices(project, actor, data)


def test_invalid_coordinates_never_reach_routes():
    for latitude in (True, float("nan"), float("inf"), 91, "19.1"):
        with pytest.raises(ValueError):
            coordinates({"latitude": latitude, "longitude": 72.9})


def test_no_google_key_fails_closed(settings):
    settings.GOOGLE_MAPS_API_KEY = ""
    with pytest.raises(ValueError, match="not configured"):
        GoogleProjectMaps().search("Example project, Thane, Maharashtra")


@pytest.mark.parametrize("reason", ["SERVICE_DISABLED", "API_KEY_SERVICE_BLOCKED", "BILLING_DISABLED", "UNKNOWN_REASON"])
def test_maps_error_reasons_do_not_expose_provider_payload(settings, monkeypatch, reason):
    from io import BytesIO
    from urllib.error import HTTPError
    from platform_api import external_sources

    settings.GOOGLE_MAPS_API_KEY = "private-test-key"
    payload = {"error": {"message": "private-test-key private-provider-text",
        "details": [{"reason": reason, "metadata": {"consumer": "private-project"}}]}}
    monkeypatch.setattr(external_sources, "urlopen", Mock(side_effect=HTTPError(
        "https://routes.googleapis.com/directions/v2:computeRoutes", 403, "Denied", {},
        BytesIO(json.dumps(payload).encode()))))
    result = GoogleProjectMaps().route({"latitude": 19.1, "longitude": 72.9},
                                      {"latitude": 19.2, "longitude": 72.9}, "WALK")
    assert result["status"] == "unavailable" and "HTTP 403" in result["error"]
    assert "private" not in result["error"]
    assert (reason in result["error"]) == (reason != "UNKNOWN_REASON")


@pytest.mark.parametrize("link", [
    "https://www.google.com/maps/search/?api=1&query=19.15%2C72.95",
    "https://www.google.com/maps?q=19.15,72.95",
    "https://maps.google.com/?q=19.15,72.95",
    "https://www.google.com/maps/place/Project/@20,73,17z/data=!3d19.15!4d72.95",
])
def test_direct_location_links_use_pin_coordinates_without_api(link, settings):
    from platform_api.external_sources_admin import DirectLocationForm

    settings.GOOGLE_MAPS_API_KEY = ""
    form = DirectLocationForm({"maps_url": link})
    assert form.is_valid(), form.errors
    assert form.cleaned_data["reference"]["location"] == {"latitude": 19.15, "longitude": 72.95}


@pytest.mark.parametrize("values", [
    {}, {"latitude": "19"}, {"latitude": "NaN", "longitude": "72"},
    {"latitude": "91", "longitude": "72"}, {"latitude": "19", "longitude": "181"},
    {"maps_url": "https://www.google.com/maps/@19.15,72.95,15z"},
    {"maps_url": "https://www.google.com/maps/dir/?query=19.15,72.95"},
    {"maps_url": "https://google.com.evil.example/maps/?q=19.15,72.95"},
    {"maps_url": "https://127.0.0.1/maps/?q=19.15,72.95"},
    {"maps_url": "https://www.google.com/maps?q=19,72", "latitude": "19", "longitude": "72"},
])
def test_direct_location_rejects_invalid_or_ambiguous_inputs(values):
    from platform_api.external_sources_admin import DirectLocationForm

    assert not DirectLocationForm(values).is_valid()


@pytest.mark.parametrize("draft", [None, {}, {"selected": {}}, {"manualLocation": {"location": {"latitude": float("nan"), "longitude": 72}}}])
def test_map_preview_rejects_missing_or_invalid_locations(draft):
    from platform_api.external_sources_admin import draft_map_url

    assert draft_map_url(draft) == ""


def test_map_preview_selected_match_uses_only_coordinates():
    from urllib.parse import parse_qs, urlsplit
    from platform_api.external_sources_admin import draft_map_url

    preview = draft_map_url({"selected": {"name": "private draft text", "placeId": "private-id", "location": {"latitude": 19.1, "longitude": 72.9}}})
    assert urlsplit(preview).netloc == "www.openstreetmap.org"
    assert parse_qs(urlsplit(preview).query)["marker"] == ["19.1,72.9"]
    assert "private" not in preview


def test_project_transit_maps_select_nearest_across_metro_statuses(monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    from platform_api import external_sources_admin as sources

    def station(name, kind, distance, status="operational"):
        return {"name": name, "kind": kind, "serviceStatus": status,
                "location": {"latitude": 19.2, "longitude": 72.9},
                "walking": {"status": "available", "distanceMeters": distance}}

    routes = {"groups": [{"stations": [station("Existing metro", "metro", 1400),
        station("Upcoming metro", "metro", 700, "under_construction"),
        station("Far railway", "railway", 3500), station("Near railway", "railway", 1800),
        station("Invalid distance", "metro", -1), station("Invalid bool", "railway", True),
        {"kind": "metro", "walking": {"status": "unavailable"}}, None]}]}
    monkeypatch.setattr(sources, "saved_station_routes", lambda project: routes)
    result = sources.project_transit_maps(SimpleNamespace(latitude=19.1, longitude=72.8))
    metro, railway = result["views"]
    assert metro["station"] == "Upcoming metro" and metro["service_status"] == "Upcoming: under construction"
    assert railway["station"] == "Near railway"
    assert urlsplit(metro["embed_url"]).netloc == "maps.google.com"
    query = parse_qs(urlsplit(metro["embed_url"]).query)
    assert query == {"saddr": ["19.1,72.8"], "daddr": ["19.2,72.9"], "dirflg": ["w"], "output": ["embed"]}
    assert "travelmode=walking" in metro["directions_url"]
    routes["originPlaceId"] = "building-id"
    routes["groups"][0]["stations"][1]["placeId"] = "station-id"
    routes["groups"][0]["stations"][1]["walking"]["endpointMode"] = "places"
    metro = sources.project_transit_maps(SimpleNamespace(latitude=19.1, longitude=72.8))["views"][0]
    link = parse_qs(urlsplit(metro["directions_url"]).query)
    assert link["origin_place_id"] == ["building-id"]
    assert link["destination_place_id"] == ["station-id"]
    routes["groups"][0]["stations"][1]["walking"]["endpointMode"] = "coordinates"
    metro = sources.project_transit_maps(SimpleNamespace(latitude=19.1, longitude=72.8))["views"][0]
    assert "origin_place_id" not in parse_qs(urlsplit(metro["directions_url"]).query)
    routes["origin"] = {"latitude": 19.3, "longitude": 72.7}
    metro = sources.project_transit_maps(SimpleNamespace(latitude=19.1, longitude=72.8))["views"][0]
    assert metro["different_origin"]
    assert parse_qs(urlsplit(metro["embed_url"]).query)["saddr"] == ["19.3,72.7"]
    routes["origin"] = {"latitude": 99, "longitude": 72.7}
    assert sources.project_transit_maps(SimpleNamespace(latitude=19.1, longitude=72.8))["views"][0]["station"] is None


@pytest.mark.django_db
def test_transit_route_denial_is_visible_and_escaped(client, django_user_model, monkeypatch):
    from django.urls import reverse
    from platform_api import external_sources_admin as sources

    actor = django_user_model.objects.create_superuser(username="route-diagnostics", password="test-only")
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Route diagnostics"),
        submitted_name="Pinned project", latitude=19.1, longitude=72.9)
    error = "Google Maps request failed (HTTP 403). Check API access, billing and quota."
    station = {"kind": "metro", "walking": {"status": "unavailable", "error": error}}
    monkeypatch.setattr(sources, "saved_station_routes", lambda project: {
        "groups": [{"stations": [station, station]}], "errors": ["<provider error>"]})
    result = sources.project_transit_maps(project)
    assert result["views"][0]["errors"] == ["<provider error>", error]
    assert result["views"][0]["access_denied"]
    assert result["views"][0]["station"] is None
    assert not result["views"][1]["access_denied"]
    client.force_login(actor)
    response = client.get(reverse("admin:platform_api_project_change", args=[project.pk]))
    assert response.status_code == 200
    html = response.content.decode()
    assert "Directions lookup failed" in html and "HTTP 403" in html
    assert "Routes API is enabled" in html
    assert "&lt;provider error&gt;" in html and "<provider error>" not in html


@pytest.mark.django_db
@pytest.mark.parametrize("latitude,longitude", [(None, None), (91, 72), (float("nan"), 72), (19, 72)])
def test_project_transit_maps_missing_data_has_empty_views(monkeypatch, latitude, longitude):
    from platform_api import external_sources_admin as sources

    monkeypatch.setattr(sources, "saved_station_routes", lambda project: None)
    result = sources.project_transit_maps(Project(pk=uuid4(), latitude=latitude, longitude=longitude))
    assert result["has_location"] == (latitude == 19)
    assert all(view["station"] is None for view in result["views"])


@pytest.mark.django_db
@pytest.mark.parametrize("valid_pin", [True, False])
def test_project_transit_maps_preview_manual_pin_without_approval(client, django_user_model, monkeypatch, valid_pin):
    from urllib.parse import parse_qs, urlsplit
    from django.urls import reverse
    from platform_api import external_sources_admin as sources

    actor = django_user_model.objects.create_superuser(username="pin-preview", password="test-only")
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Pin preview"),
                                     submitted_name="Manual pin project")
    task = ReviewTask.objects.create(project=project, task_type="external_sources", state="open",
        resolution={"manualLocation": {"location": {"latitude": 19.15 if valid_pin else 99, "longitude": 72.95}},
                    "reviewState": "unreviewed"})
    before_project = Project.objects.values().get(pk=project.pk)
    before_task = ReviewTask.objects.values().get(pk=task.pk)
    monkeypatch.setattr(sources, "saved_station_routes", lambda project: None)
    result = sources.project_transit_maps(project)
    assert result["has_location"] == valid_pin
    if valid_pin:
        assert parse_qs(urlsplit(result["pin_embed_url"]).query)["q"] == ["19.15,72.95"]
        assert "not applied" in result["location_label"]
    else:
        assert "pin_embed_url" not in result
    client.force_login(actor)
    response = client.get(reverse("admin:platform_api_project_change", args=[project.pk]))
    assert response.status_code == 200
    assert (b'title="Project pin - Metro view"' in response.content) == valid_pin
    assert (b'title="Project pin - Railway view"' in response.content) == valid_pin
    assert (b"No saved project location" in response.content) != valid_pin
    assert Project.objects.values().get(pk=project.pk) == before_project
    assert ReviewTask.objects.values().get(pk=task.pk) == before_task


@pytest.mark.django_db
def test_manual_pin_route_generation_preserves_review_and_rejects_changed_reference(monkeypatch, django_user_model):
    from platform_api import external_sources_admin as sources

    actor = django_user_model.objects.create_superuser(username="manual-routes", password="test-only")
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Manual routes"),
                                     submitted_name="Pinned project")
    task = ReviewTask.objects.create(project=project, task_type="external_sources", state="open",
        resolution={"manualLocation": {"location": {"latitude": 19.15, "longitude": 72.95}},
                    "submittedBy": actor.pk, "reviewState": "unreviewed"})
    before_project = Project.objects.values().get(pk=project.pk)
    details = Mock(side_effect=AssertionError("Manual pins do not need Place Details"))
    monkeypatch.setattr(GoogleProjectMaps, "details", details)
    provider = Mock(return_value={"groups": [{"stations": []}], "errors": []})
    monkeypatch.setattr(sources, "station_routes", provider)
    routes = sources.refresh_google_routes(project, actor, task.pk, 10000)
    assert routes["origin"] == {"latitude": 19.15, "longitude": 72.95}
    assert provider.call_args.args[0]["location"] == routes["origin"]
    assert sources.saved_station_routes(project) == routes
    task.refresh_from_db()
    assert task.state == "open" and task.resolution["reviewState"] == "unreviewed"
    assert task.version == 2
    assert Project.objects.values().get(pk=project.pk) == before_project
    assert AuditEvent.objects.filter(action="project.google_station_routes_refreshed", entity_id=task.pk).count() == 1

    def changed_during_lookup(*args):
        ReviewTask.objects.filter(pk=task.pk).update(version=3)
        return {"groups": [], "errors": []}

    provider.side_effect = changed_during_lookup
    with pytest.raises(ValueError, match="changed"):
        sources.refresh_google_routes(project, actor, task.pk, 10000)
    task.refresh_from_db()
    assert task.resolution["stationRoutes"] == routes
    details.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("provider_failure", [False, True])
def test_commit_pin_generates_directions_and_retry_returns_to_project(client, django_user_model, settings, tmp_path, monkeypatch, provider_failure):
    from django.core.cache import cache
    from django.urls import reverse
    from platform_api import external_sources_admin as sources

    settings.WORKSPACE_DATA_ROOT = tmp_path
    settings.GOOGLE_MAPS_API_KEY = "test-only"
    actor = django_user_model.objects.create_superuser(username="pin-route-submit", password="test-only")
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Pin generation"),
                                     submitted_name="Pinned project")
    token = str(uuid4())
    cache.set(draft_key(project, actor, token), {"projectVersion": project.updated_at.isoformat(),
        "manualLocation": {"location": {"latitude": 19.15, "longitude": 72.95}}}, 900)
    station = {"name": "Nearby station", "kind": "railway", "serviceStatus": "operational",
        "location": {"latitude": 19.16, "longitude": 72.96},
        "walking": {"status": "available", "distanceMeters": 1000}}
    provider = Mock(return_value={"groups": [{"stations": [station]}], "errors": []})
    if provider_failure:
        provider.side_effect = ValueError("Provider failed")
    monkeypatch.setattr(sources, "station_routes", provider)
    client.force_login(actor)
    url = reverse("admin:platform_api_project_external_sources", args=[project.pk])
    response = client.post(url, {"action": "commit", "draft_token": token, "confirm": "on"})
    assert response.status_code == 302
    task = ReviewTask.objects.get(project=project)
    assert ("stationRoutes" in task.resolution) != provider_failure
    assert task.resolution["reviewState"] == "unreviewed"
    assert provider.call_count == 1
    client.post(url, {"action": "commit", "draft_token": token, "confirm": "on"})
    assert provider.call_count == 1
    change_url = reverse("admin:platform_api_project_change", args=[project.pk])
    html = client.get(change_url).content.decode()
    assert "Generate directions" in html and f'formaction="{url}"' in html
    provider.side_effect = None
    response = client.post(url, {"action": "retry_saved_routes", "reference_id": str(task.pk),
                                 "return_to": "project", "radius": "10000"})
    assert response.status_code == 302 and response.url == change_url + "#project-transit-heading"
    assert provider.call_count == 2
    assert sources.project_transit_maps(project)["views"][1]["station"] == "Nearby station"
    project.refresh_from_db()
    assert project.latitude is None and project.longitude is None
    task.refresh_from_db()
    assert task.state == "open" and task.resolution["reviewState"] == "unreviewed"


@pytest.mark.django_db
@pytest.mark.parametrize("restriction", ["missing_key", "view_only", "foreign_reference", "retired"])
def test_generate_pin_routes_rejects_invalid_requests(client, django_user_model, settings, monkeypatch, restriction):
    from django.contrib.auth.models import Permission
    from django.urls import reverse
    from platform_api import external_sources_admin as sources

    settings.GOOGLE_MAPS_API_KEY = "" if restriction == "missing_key" else "test-only"
    actor = django_user_model.objects.create_user(username="route-guard", is_staff=True, is_superuser=restriction != "view_only")
    if restriction == "view_only":
        actor.user_permissions.add(Permission.objects.get(codename="view_project", content_type__app_label="platform_api"))
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Guard tests"),
        submitted_name="Pinned project", status="retired" if restriction == "retired" else "ingestion")
    task = ReviewTask.objects.create(project=project, task_type="external_sources",
        resolution={"manualLocation": {"location": {"latitude": 19.15, "longitude": 72.95}}})
    provider = Mock(side_effect=AssertionError("Unauthorized provider call"))
    monkeypatch.setattr(sources, "station_routes", provider)
    client.force_login(actor)
    response = client.post(reverse("admin:platform_api_project_external_sources", args=[project.pk]),
        {"action": "retry_saved_routes", "reference_id": str(uuid4() if restriction == "foreign_reference" else task.pk)})
    assert response.status_code == (403 if restriction == "view_only" else 200)
    provider.assert_not_called()
    task.refresh_from_db()
    assert "stationRoutes" not in task.resolution


def test_direct_location_short_link_uses_restricted_resolver(monkeypatch):
    from platform_api.external_sources_admin import DirectLocationForm
    from platform_api.management.commands import import_google_project_place_decisions as command

    resolver = Mock(return_value={"name": "Project", "location": {"latitude": 19.15, "longitude": 72.95}})
    monkeypatch.setattr(command, "resolve_share_url", resolver)
    form = DirectLocationForm({"maps_url": "https://maps.app.goo.gl/project"})
    assert form.is_valid(), form.errors
    resolver.assert_called_once_with("https://maps.app.goo.gl/project")
    assert form.cleaned_data["reference"]["evidenceMethod"] == "manual_maps_link"
    assert len(form.cleaned_data["reference"]["sourceUrlDigest"]) == 64
    resolver.side_effect = ValueError("Google Maps share link could not be resolved.")
    assert not DirectLocationForm({"maps_url": "https://maps.app.goo.gl/project"}).is_valid()


@pytest.mark.django_db
def test_direct_location_draft_commit_and_independent_approval(client, django_user_model, settings, monkeypatch, tmp_path):
    from urllib.parse import parse_qs, urlsplit
    from django.urls import reverse

    settings.GOOGLE_MAPS_API_KEY = ""
    settings.PROJECT_LOCATION_REQUIRE_SECOND_APPROVER = True
    settings.WORKSPACE_DATA_ROOT = tmp_path
    provider = Mock(side_effect=AssertionError("Direct location must not call Google APIs"))
    monkeypatch.setattr(GoogleProjectMaps, "_request", provider)
    actor = django_user_model.objects.create_superuser(username="direct-author", password="test-only")
    reviewer = django_user_model.objects.create_superuser(username="direct-reviewer", password="test-only")
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Direct locations"), submitted_name="Manual project")
    url = reverse("admin:platform_api_project_external_sources", args=[project.pk])
    client.force_login(actor)
    response = client.get(url)
    assert b"Direct project location" in response.content and b"Preview direct location" in response.content
    assert client.post(url, {"action": "direct_location", "latitude": "19.15"}).status_code == 200
    assert not ReviewTask.objects.exists()
    response = client.post(url, {"action": "direct_location", "latitude": "19.15", "longitude": "72.95"})
    assert response.status_code == 302
    token = parse_qs(urlsplit(response.url).query)["draft"][0]
    preview = client.get(response.url)
    assert b"View pin on Google Maps" in preview.content
    assert b'title="Draft project location map"' in preview.content
    assert b"Latitude 19.15" not in preview.content
    assert parse_qs(urlsplit(preview.context["draft_map_url"]).query)["marker"] == ["19.15,72.95"]
    assert preview["Referrer-Policy"] == "same-origin"
    assert {"private", "no-store"} <= set(preview["Cache-Control"].split(", "))
    assert "frame-src https://www.openstreetmap.org" in preview["Content-Security-Policy"]
    assert "frame-src" not in client.get(reverse("admin:platform_api_project_changelist"))["Content-Security-Policy"]
    response = client.post(url, {"action": "commit", "draft_token": token, "confirm": "on"})
    assert response.status_code == 302
    project.refresh_from_db()
    assert project.latitude is None and project.longitude is None
    task = ReviewTask.objects.get(project=project)
    assert task.resolution["manualLocation"]["location"] == {"latitude": 19.15, "longitude": 72.95}
    assert task.resolution["submittedBy"] == actor.pk and task.resolution["reviewState"] == "unreviewed"
    assert "googlePlaceId" not in task.resolution
    listing = client.get(reverse("admin:platform_api_project_changelist"), {"google_match": "needs_lookup"})
    assert not listing.context["cl"].queryset.exists()
    data = {"action": "apply_manual", "confirm": "on", "reference_id": str(task.pk),
            "project_version": project.updated_at.isoformat(), "reference_version": task.version}
    assert b"different reviewer" in client.post(url, data).content
    client.force_login(reviewer)
    assert b"Apply reviewed location" in client.get(url).content
    assert client.post(url, data).status_code == 302
    project.refresh_from_db()
    task.refresh_from_db()
    assert float(project.latitude) == 19.15 and float(project.longitude) == 72.95
    assert task.state == "resolved" and task.resolution["acceptedBy"] == reviewer.pk
    assert task.resolution["approvalPolicy"] == {"secondApproverRequired": True, "selfApproved": False}
    assert project.status == "draft" and project.published_fact_set_version == 0
    assert AuditEvent.objects.filter(action="project.manual_location_accepted").count() == 1
    provider.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("case,second_approver_required", [
    (case, required) for case in ("stale_project", "stale_reference", "other_project", "retired")
    for required in (True, False)
] + [("self_review", True)])
def test_manual_reference_approval_rejects_unsafe_changes(django_user_model, case, settings, second_approver_required):
    from platform_api.external_sources import direct_location
    from platform_api.external_sources_admin import apply_manual_reference

    settings.PROJECT_LOCATION_REQUIRE_SECOND_APPROVER = second_approver_required
    reviewer = django_user_model.objects.create_superuser(username="manual-approval-reviewer", password="test-only")
    author = django_user_model.objects.create_user(username="manual-location-author", is_staff=True)
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Approval guards"), submitted_name="Guarded project")
    task = ReviewTask.objects.create(project=project, task_type="external_sources", resolution={
        "manualLocation": direct_location(19.15, 72.95), "submittedBy": author.pk, "reviewState": "unreviewed"})
    project_version, task_version = project.updated_at, task.version
    if case == "stale_project":
        project.save()
    elif case == "stale_reference":
        task.version += 1
        task.save()
    elif case == "other_project":
        project = Project.objects.create(organization=project.organization, submitted_name="Other project")
        project_version = project.updated_at
    elif case == "retired":
        project.status = "retired"
        project.save()
        project_version = project.updated_at
    else:
        task.resolution["submittedBy"] = reviewer.pk
        task.save()
    with pytest.raises(ValueError):
        apply_manual_reference(project, reviewer, task.pk, project_version, task_version)
    project.refresh_from_db()
    task.refresh_from_db()
    assert project.latitude is None and task.state == "open"
    assert not AuditEvent.objects.filter(action="project.manual_location_accepted").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("lookup_status", ["ambiguous", "no_match", "failed", "not_run"])
@pytest.mark.parametrize("reference_kind", ["manual", "google"])
def test_saved_location_leaves_unresolved_lists_for_review(client, django_user_model, settings, tmp_path, lookup_status, reference_kind):
    from django.urls import reverse
    from platform_api.external_sources import direct_location

    settings.WORKSPACE_DATA_ROOT = tmp_path
    path = tmp_path / "output" / "reports" / "google-project-places.json"
    path.parent.mkdir(parents=True)
    actor = django_user_model.objects.create_superuser(username="location-list-reviewer", password="test-only")
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Location lists"), submitted_name="Manual location project")
    path.write_text(json.dumps({"projects": [{"projectId": str(project.pk), "status": lookup_status}]}))
    original_report = path.read_bytes()
    client.force_login(actor)
    url = reverse("admin:platform_api_project_changelist")
    assert client.get(url, {"google_match": lookup_status}).context["cl"].queryset.filter(pk=project.pk).exists()
    resolution = {"reviewState": "unreviewed"}
    resolution.update({"manualLocation": direct_location(19.15, 72.95)} if reference_kind == "manual" else {"googlePlaceId": "test-place"})
    task = ReviewTask.objects.create(project=project, task_type="external_sources", resolution=resolution)
    for filter_value in (lookup_status, "needs_lookup", "missing_reference"):
        response = client.get(url, {"google_match": filter_value})
        assert response.status_code == 200
        assert not response.context["cl"].queryset.filter(pk=project.pk).exists()
    response = client.get(url, {"google_match": "awaiting_review"})
    assert response.status_code == 200 and response.context["cl"].queryset.filter(pk=project.pk).exists()
    assert b"Maps: incomplete data" in response.content
    assert client.get(url, {"google_match": "incomplete"}).context["cl"].queryset.filter(pk=project.pk).exists()
    task.resolution["reviewState"] = "accepted"
    task.state = "resolved"
    task.save()
    assert not client.get(url, {"google_match": "awaiting_review"}).context["cl"].queryset.filter(pk=project.pk).exists()
    assert not client.get(url, {"google_match": lookup_status}).context["cl"].queryset.filter(pk=project.pk).exists()
    assert client.get(url, {"google_match": "incomplete"}).context["cl"].queryset.filter(pk=project.pk).exists()
    project.latitude, project.longitude = 19.15, 72.95
    project.save()
    assert not client.get(url, {"google_match": "incomplete"}).context["cl"].queryset.filter(pk=project.pk).exists()
    assert path.read_bytes() == original_report
    project.refresh_from_db()
    assert float(project.latitude) == 19.15


@pytest.mark.django_db
@pytest.mark.parametrize("second_approver_required", [True, False])
@pytest.mark.parametrize("source", ["manual", "google", "enrich"])
def test_location_approval_policy(client, django_user_model, settings, monkeypatch, second_approver_required, source):
    from django.urls import reverse
    from platform_api import external_sources_admin as sources
    from platform_api.external_sources import direct_location

    settings.PROJECT_LOCATION_REQUIRE_SECOND_APPROVER = second_approver_required
    actor = django_user_model.objects.create_superuser(username="single-location-reviewer", password="test-only")
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Approval policy"), submitted_name="Policy project")
    resolution = {"submittedBy": actor.pk, "reviewState": "unreviewed"}
    resolution.update({"manualLocation": direct_location(19.15, 72.95)} if source == "manual" else {"googlePlaceId": "policy-place"})
    task = ReviewTask.objects.create(project=project, task_type="external_sources", resolution=resolution)
    details = Mock(return_value={"placeId": "policy-place", "location": {"latitude": 19.15, "longitude": 72.95}})
    monkeypatch.setattr(sources.GoogleProjectMaps, "details", details)
    monkeypatch.setattr(sources, "station_routes", Mock(return_value={"groups": [], "errors": []}))
    client.force_login(actor)
    response = client.get(reverse("admin:platform_api_project_external_sources", args=[project.pk]))
    action = b'value="apply_manual"' if source == "manual" else b'value="apply_saved"'
    assert (action in response.content) is not second_approver_required

    def approve():
        if source == "manual":
            return sources.apply_manual_reference(project, actor, task.pk, project.updated_at, task.version)
        if source == "google":
            return sources.apply_google_reference(project, actor, task.pk)
        return sources.enrich_google_reference(project, actor, task.pk, 10000)

    if second_approver_required:
        with pytest.raises(ValueError, match="different reviewer"):
            approve()
        details.assert_not_called()
        assert not AuditEvent.objects.exists()
    else:
        approve()
        project.refresh_from_db()
        task.refresh_from_db()
        assert task.state == "resolved" and float(project.latitude) == 19.15
        assert task.resolution["acceptedBy"] == actor.pk
        assert task.resolution["approvalPolicy"] == {"secondApproverRequired": False, "selfApproved": True}
        assert AuditEvent.objects.get().metadata["selfApproved"] is True
        assert project.status == "draft"


def test_google_route_distinguishes_no_route_and_failure(monkeypatch):
    maps = GoogleProjectMaps()
    origin = {"latitude": 19.2, "longitude": 72.9}
    request = Mock(return_value={"routes": []})
    monkeypatch.setattr(maps, "_request", request)
    assert maps.route(origin, origin, "WALK")["status"] == "no_route"
    request.side_effect = ValueError("Provider down")
    unavailable = maps.route(origin, origin, "WALK")
    assert unavailable["status"] == "unavailable"
    assert unavailable["error"] == "Provider down"
    request.side_effect = None
    request.return_value = {"routes": [{"distanceMeters": 800, "duration": "600s"}]}
    result = maps.route(origin, origin, "DRIVE")
    assert result["distanceMeters"] == 800 and result["minutes"] == 10
    assert "travelmode=driving" in result["directionsUrl"]


@pytest.mark.django_db
def test_route_ranking_and_authority_status_override(settings):
    settings.PROJECT_MAP_STATION_CANDIDATES_FILE = ""
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
def test_project_source_columns_and_filters(client, django_user_model, settings, tmp_path):
    from django.urls import reverse

    settings.WORKSPACE_DATA_ROOT = tmp_path
    report_path = tmp_path / "output" / "reports" / "google-project-places.json"
    report_path.parent.mkdir(parents=True)
    owner = django_user_model.objects.create_superuser(username="source-columns", password="test-only")
    organization = Organization.objects.create(legal_name="Source columns")
    ambiguous = Project.objects.create(organization=organization, submitted_name="Ambiguous project")
    unmatched = Project.objects.create(organization=organization, submitted_name="Unmatched project")
    matched = Project.objects.create(organization=organization, submitted_name="Matched project", maha_rera_id="P51700053528")
    report_path.write_text(json.dumps({"projects": [
        {"projectId": str(ambiguous.pk), "status": "ambiguous"},
        {"projectId": str(unmatched.pk), "status": "no_match"},
        {"projectId": str(matched.pk), "status": "matched"},
    ]}))
    ReviewTask.objects.create(project=matched, task_type="external_sources", resolution={
        "googlePlaceId": "test-place", "reviewState": "unreviewed"})
    client.force_login(owner)
    url = reverse("admin:platform_api_project_changelist")
    for filters, expected in [({"google_match": "ambiguous"}, {ambiguous.pk}),
                              ({"google_match": "no_match"}, {unmatched.pk}),
                              ({"google_match": "needs_lookup"}, {ambiguous.pk, unmatched.pk}),
                              ({"maharera": "missing"}, {ambiguous.pk, unmatched.pk}),
                              ({"maharera": "present"}, {matched.pk})]:
        response = client.get(url, filters)
        assert response.status_code == 200
        assert {project.pk for project in response.context["cl"].queryset} == expected
    response = client.get(url)
    assert b"Google Maps" in response.content and b"MahaRERA" in response.content
    assert b"unreviewed" in response.content and b"No strict match" in response.content
    ReviewTask.objects.create(project=unmatched, task_type="external_sources", resolution={"googlePlaceId": "manual-place"})
    assert {project.pk for project in client.get(url, {"google_match": "needs_lookup"}).context["cl"].queryset} == {ambiguous.pk}
    report_path.write_text("invalid JSON")
    response = client.get(url)
    assert response.status_code == 200 and b"No lookup result" in response.content


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


@pytest.fixture
def google_reference(django_user_model):
    actor = django_user_model.objects.create_superuser(username="google-reviewer", password="test-only")
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Google tests"), submitted_name="Test project")
    task = ReviewTask.objects.create(project=project, task_type="external_sources", resolution={
        "googlePlaceId": "test-place", "reviewState": "unreviewed"})
    return project, actor, task


@pytest.mark.django_db(transaction=True)
def test_google_approval_and_route_refresh_are_explicit_and_versioned(google_reference, monkeypatch):
    from django.db import connection
    from platform_api import external_sources_admin as sources

    project, actor, task = google_reference
    def details(place_id):
        assert not connection.in_atomic_block
        return {"placeId": place_id, "location": {"latitude": 19.2, "longitude": 72.9}}
    monkeypatch.setattr(sources.GoogleProjectMaps, "details", lambda self, place_id: details(place_id))
    routes = {"groups": [], "errors": []}
    monkeypatch.setattr(sources, "station_routes", lambda *args: routes)
    sources.refresh_google_routes(project, actor, task.pk, 10000)
    project.refresh_from_db()
    task.refresh_from_db()
    assert project.latitude is None and task.state == "open"
    assert sources.saved_station_routes(project) == routes
    version = task.version
    sources.apply_google_reference(project, actor, task.pk)
    project.refresh_from_db()
    task.refresh_from_db()
    assert float(project.latitude) == 19.2 and float(project.longitude) == 72.9
    assert task.state == "resolved" and task.version == version + 1
    assert task.resolution["acceptedBy"] == actor.pk
    assert AuditEvent.objects.filter(action="project.google_location_accepted").count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("case", ["retired", "self_review", "concurrent", "other_project"])
def test_google_approval_rejects_unsafe_or_stale_reviews(google_reference, monkeypatch, case, settings):
    from platform_api import external_sources_admin as sources

    settings.PROJECT_LOCATION_REQUIRE_SECOND_APPROVER = True
    project, actor, task = google_reference
    if case == "retired":
        Project.objects.filter(pk=project.pk).update(status="retired")
    elif case == "self_review":
        task.resolution["submittedBy"] = actor.pk
        task.save()
    elif case == "other_project":
        project = Project.objects.create(organization=project.organization, submitted_name="Other")
    def details(self, place_id):
        if case == "concurrent":
            ReviewTask.objects.filter(pk=task.pk).update(version=task.version + 1)
        return {"placeId": place_id, "location": {"latitude": 19.2, "longitude": 72.9}}
    monkeypatch.setattr(sources.GoogleProjectMaps, "details", details)
    with pytest.raises(ValueError):
        sources.apply_google_reference(project, actor, task.pk)
    project.refresh_from_db()
    assert project.latitude is None and not AuditEvent.objects.exists()


@pytest.mark.django_db
def test_saved_reference_admin_controls_and_escaped_diagnostics(google_reference, client):
    from django.urls import reverse

    project, actor, task = google_reference
    task.resolution["stationRoutes"] = {"groups": [], "errors": ["<script>provider</script>"]}
    task.save()
    client.force_login(actor)
    response = client.get(reverse("admin:platform_api_project_external_sources", args=[project.pk]))
    assert response.status_code == 200
    assert b"Saved station directions" in response.content
    assert b'value="apply_saved"' in response.content and b'value="retry_saved_routes"' in response.content
    assert b"&lt;script&gt;provider&lt;/script&gt;" in response.content
    assert b"<script>provider</script>" not in response.content
    response = client.post(reverse("admin:platform_api_project_external_sources", args=[project.pk]), {
        "action": "apply_saved", "reference_id": str(task.pk)})
    assert response.status_code == 200 and b"Confirm applying" in response.content
    project.refresh_from_db()
    assert project.latitude is None


def test_strict_google_matching_requires_unique_name_and_locality():
    from platform_api.management.commands.refresh_google_project_places import strict_match

    project = SimpleNamespace(submitted_name="Test Gardens", locality="Thane")
    match = {"placeId": "one", "name": "Test Gardens", "address": "Thane, Maharashtra"}
    assert strict_match(project, [match])[0] == match
    assert strict_match(project, [match, dict(match, placeId="two")])[1] == "ambiguous"
    assert strict_match(project, [dict(match, address="Pune")])[1] == "no_match"


@pytest.mark.django_db
def test_places_command_checkpoint_resume_and_review_gate(google_reference, settings, monkeypatch, tmp_path):
    from django.core.management import call_command
    from platform_api.management.commands import refresh_google_project_places as command

    project, actor, task = google_reference
    task.delete()
    project.locality = "Thane"
    project.save()
    Project.objects.create(organization=project.organization, submitted_name="Retired", locality="Thane", status="retired")
    Project.objects.create(organization=project.organization, submitted_name="Merged", locality="Thane", address={"mergedIntoProjectId": str(project.pk)})
    settings.GOOGLE_MAPS_API_KEY = "test-only"
    search = Mock(return_value=[{"placeId": "matched-place", "name": project.submitted_name, "address": "Thane"}])
    monkeypatch.setattr(command.GoogleProjectMaps, "search", search)
    monkeypatch.setattr(command.time, "sleep", lambda *args: None)
    report = tmp_path / "nested" / "places.json"
    call_command("refresh_google_project_places", report=report)
    assert not report.exists() and not search.called
    call_command("refresh_google_project_places", execute=True, limit=1, report=report)
    project.refresh_from_db()
    task = ReviewTask.objects.get(project=project)
    assert task.state == "open" and task.resolution["reviewState"] == "unreviewed"
    assert task.resolution["evidenceMethod"] == "google_places_strict_match" and project.latitude is None
    call_command("refresh_google_project_places", execute=True, resume=True, report=report)
    assert search.call_count == 1 and ReviewTask.objects.count() == 1
    assert not list(report.parent.glob("*.tmp"))


@pytest.mark.parametrize("url", ["http://maps.app.goo.gl/test", "https://maps.app.goo.gl:444/test",
    "https://user@maps.app.goo.gl/test", "https://maps.app.goo.gl.evil.test/test", "https://127.0.0.1/",
    "https://www.google.com/url?q=https://127.0.0.1/"])
def test_share_url_rejects_unsafe_initial_or_redirect_targets(url):
    from urllib.request import Request
    from platform_api.management.commands.import_google_project_place_decisions import MapsRedirectHandler, validate_maps_url

    with pytest.raises(ValueError):
        validate_maps_url(url, initial=True)
    with pytest.raises(ValueError):
        MapsRedirectHandler().redirect_request(Request("https://maps.app.goo.gl/test"), None, 302, "", {}, url)


def test_share_url_uses_place_not_viewport_coordinates(monkeypatch):
    from platform_api.management.commands import import_google_project_place_decisions as command

    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(command, "build_opener", lambda *args: opener)
    response.geturl.return_value = "https://www.google.com/maps/place/Test+Gardens/@10,20,15z/data=!3d19.2!4d72.9"
    result = command.resolve_share_url("https://maps.app.goo.gl/test")
    assert result == {"name": "Test Gardens", "location": {"latitude": 19.2, "longitude": 72.9}}
    response.geturl.return_value = "https://www.google.com/maps/place/Test/@10,20,15z"
    with pytest.raises(ValueError, match="viewport"):
        command.resolve_share_url("https://maps.app.goo.gl/test")


@pytest.mark.django_db
def test_share_import_is_bounded_project_bound_and_resumable(google_reference, settings, monkeypatch, tmp_path):
    from django.core.management import call_command, CommandError
    from platform_api.management.commands import import_google_project_place_decisions as command

    project, actor, task = google_reference
    task.delete()
    settings.GOOGLE_MAPS_API_KEY = "test-only"
    projects = [project] + [Project.objects.create(organization=project.organization, submitted_name=f"Project {index}") for index in range(5)]
    source_path, decisions_path, report_path = (tmp_path / name for name in ("source.json", "decisions.json", "import.json"))
    command.write_report(source_path, {"projects": [{"projectId": str(item.pk), "status": "no_match", "candidateCount": 2} for item in projects]})
    decisions = [{"index": index, "projectId": str(item.pk), "action": "select", "url": f"https://maps.app.goo.gl/test{index}"} for index, item in enumerate(projects, 1)]
    decisions_path.write_text(json.dumps(decisions), encoding="utf-8")
    location = {"latitude": 19.2, "longitude": 72.9}
    resolver = Mock(return_value={"name": "Test", "location": location})
    monkeypatch.setattr(command, "resolve_share_url", resolver)
    monkeypatch.setattr(command.GoogleProjectMaps, "search", lambda *args: [{"placeId": "matched-place", "location": location}])
    call_command("import_google_project_place_decisions", str(decisions_path), source_report=source_path, report=report_path, execute=True)
    assert resolver.call_count == 5 and ReviewTask.objects.count() == 5
    assert all(item.state == "open" for item in ReviewTask.objects.all())
    assert "https://maps.app.goo.gl" not in report_path.read_text()
    call_command("import_google_project_place_decisions", str(decisions_path), source_report=source_path, report=report_path, execute=True, resume=True)
    assert resolver.call_count == 6 and ReviewTask.objects.count() == 6
    decisions[0]["projectId"] = str(uuid4())
    decisions_path.write_text(json.dumps(decisions), encoding="utf-8")
    with pytest.raises(CommandError, match="projectId"):
        call_command("import_google_project_place_decisions", str(decisions_path), source_report=source_path, report=report_path, execute=True, resume=True)


@pytest.mark.django_db(transaction=True)
def test_route_command_five_project_cap_persists_reviews_and_resumes(google_reference, settings, monkeypatch, tmp_path):
    from django.core.management import call_command
    from django.db import connection
    from platform_api import external_sources_admin as sources
    from platform_api.management.commands import enrich_google_project_routes as command

    project, actor, task = google_reference
    for index in range(5):
        other = Project.objects.create(organization=project.organization, submitted_name=f"Project {index}")
        ReviewTask.objects.create(project=other, task_type="external_sources", resolution={"googlePlaceId": f"place-{index}"})
    Project.objects.create(organization=project.organization, submitted_name="Unavailable")
    settings.GOOGLE_MAPS_API_KEY = "test-only"
    details = Mock(side_effect=lambda place_id: {"placeId": place_id, "location": {"latitude": 19.2, "longitude": 72.9}})
    monkeypatch.setattr(sources.GoogleProjectMaps, "details", details)
    def routes(*args):
        assert not connection.in_atomic_block
        return {"groups": [{"stations": [{"walking": {"status": "available", "minutes": 5}}]}]}
    monkeypatch.setattr(sources, "station_routes", routes)
    monkeypatch.setattr(command.time, "sleep", lambda *args: None)
    report = tmp_path / "routes.json"
    call_command("enrich_google_project_routes", report=report)
    assert not details.called and not report.exists()
    call_command("enrich_google_project_routes", execute=True, reviewer=actor.username, report=report)
    assert details.call_count == 5 and ReviewTask.objects.filter(state="resolved").count() == 5
    checkpoint = json.loads(report.read_text())
    assert len(checkpoint["projects"]) == 7 and checkpoint["state"] == "paused"
    assert checkpoint["counts"]["skipped_unavailable"] == 1
    call_command("enrich_google_project_routes", execute=True, reviewer=actor.username, report=report, resume=True)
    assert details.call_count == 6 and ReviewTask.objects.filter(state="resolved").count() == 6
    assert AuditEvent.objects.filter(action="project.google_location_routes_enriched").count() == 6
    assert all("stationRoutes" in item.resolution for item in ReviewTask.objects.all())
    assert json.loads(report.read_text())["state"] == "completed"


@pytest.mark.django_db
def test_route_retry_generation_retries_unavailable_once(google_reference, settings, monkeypatch, tmp_path):
    from django.core.management import call_command, CommandError
    from platform_api import external_sources_admin as sources
    from platform_api.management.commands import enrich_google_project_routes as command

    project, actor, task = google_reference
    settings.GOOGLE_MAPS_API_KEY = "test-only"
    details = Mock(return_value={"placeId": "test-place", "location": {"latitude": 19.2, "longitude": 72.9}})
    monkeypatch.setattr(sources.GoogleProjectMaps, "details", details)
    monkeypatch.setattr(sources, "station_routes", lambda *args: {"groups": []})
    monkeypatch.setattr(command.time, "sleep", lambda *args: None)
    report = tmp_path / "routes.json"
    call_command("enrich_google_project_routes", execute=True, reviewer=actor.username, report=report)
    for iteration in range(2):
        call_command("enrich_google_project_routes", execute=True, reviewer=actor.username, report=report,
            resume=True, retry_failed=True, retry_generation="once")
    assert details.call_count == 2
    with pytest.raises(CommandError, match="between 1 and 5"):
        call_command("enrich_google_project_routes", execute=True, batch_size=6, report=report)


def test_maharera_mapping_and_url_validation(tmp_path):
    from django.core.management import CommandError
    from platform_api.management.commands.fetch_maharera_project import _store_mapping, detail_project_id

    url = "https://maharerait.maharashtra.gov.in/public/project/view/62474"
    assert detail_project_id(url) == "62474"
    for unsafe in (url.replace("https", "http"), url.replace(".gov.in", ".gov.in.evil.test"), url.replace("https://", "https://user@")):
        with pytest.raises(CommandError):
            detail_project_id(unsafe)
    path = tmp_path / "nested" / "mapping.json"
    _store_mapping(path, "PR1330002502719", "62474", url)
    _store_mapping(path, "P51700053528", "12345", url.replace("62474", "12345"))
    assert len(json.loads(path.read_text())) == 2 and not list(path.parent.glob("*.tmp"))


def test_maharera_headless_captcha_never_prompts_or_extracts(monkeypatch):
    from django.core.management import CommandError
    from platform_api.management.commands.fetch_maharera_project import _captcha_handoff

    page = Mock()
    page.locator.return_value.first.is_visible.return_value = True
    prompt = Mock(side_effect=AssertionError("Headless must not prompt"))
    monkeypatch.setattr("builtins.input", prompt)
    with pytest.raises(CommandError, match="visible browser"):
        _captcha_handoff(page, True, Mock())
    assert not prompt.called and not page.evaluate.called


def test_ported_catalogue_has_dated_authority_candidates(settings, monkeypatch):
    from datetime import date
    from pathlib import Path
    from platform_api.external_sources import metro_catalogue, nearby_catalogue

    path = Path(settings.BASE_DIR) / "config" / "mmr-upcoming-metro.json"
    settings.PROJECT_METRO_CATALOGUE = json.loads(path.read_text())
    monkeypatch.setattr(timezone, "localdate", lambda: date(2026, 9, 16))
    candidates = metro_catalogue()
    assert len(candidates) == 29
    assert {row["line"] for row in candidates} == {"Metro Line 4", "Metro Line 4A", "Metro Line 2B", "Metro Line 6", "Metro Line 9"}
    assert len(nearby_catalogue({"latitude": 19.12, "longitude": 72.9}, 20000)) <= 5
    settings.PROJECT_METRO_CATALOGUE[0]["asOf"] = "2099-01-01"
    with pytest.raises(ValueError, match="future-dated"):
        metro_catalogue()


@pytest.mark.django_db
def test_connector_browser_views_and_public_extraction(google_reference, client, settings, tmp_path):
    import mimetypes
    from pathlib import Path
    from urllib.parse import urlsplit
    from django.contrib.staticfiles import finders
    from django.urls import reverse
    from platform_api.management.commands.fetch_maharera_project import _extract_project_data

    playwright_api = pytest.importorskip("playwright.sync_api")
    project, actor, task = google_reference
    station = {"name": "Station with a long candidate name", "serviceStatus": "planned", "line": "Metro Line 4",
        "sourceUrl": "https://mmrda.maharashtra.gov.in/", "statusSourceUrl": "https://mmrda.maharashtra.gov.in/",
        "asOf": "2026-09-15", "walking": {"status": "available", "distanceMeters": 450, "minutes": 6,
            "directionsUrl": "https://www.google.com/maps/dir/?api=1"},
        "driving": {"status": "unavailable", "error": "Google Maps request failed (HTTP 403). Check API access, billing and quota."}}
    task.resolution["stationRoutes"] = {"radiusMeters": 10000, "retrievedAt": timezone.now().isoformat(),
        "catalogueConfigured": True, "groups": [{"label": "Upcoming metro", "stations": [station], "nearest": {"walking": station["name"]}}]}
    task.save()
    client.force_login(actor)
    paths = [reverse("admin:platform_api_project_change", args=[project.pk]),
        reverse("admin:platform_api_project_external_sources", args=[project.pk])]
    pages = {path: client.get(path).content for path in paths}
    def respond(route):
        path = urlsplit(route.request.url).path
        if path in pages:
            route.fulfill(body=pages[path], content_type="text/html")
        elif path.startswith(settings.STATIC_URL):
            filename = finders.find(path.removeprefix(settings.STATIC_URL))
            if filename:
                route.fulfill(body=Path(filename).read_bytes(), content_type=mimetypes.guess_type(filename)[0] or "application/octet-stream")
            else:
                route.abort()
        else:
            route.abort()
    with playwright_api.sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).exists():
            pytest.skip("Install Chromium with python -m playwright install chromium")
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.route("**/*", respond)
            for path in paths:
                for width in (1440, 390):
                    page.set_viewport_size({"width": width, "height": 1000})
                    page.goto("http://connector.invalid" + path)
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    if path == paths[0]:
                        field = page.locator(".field-google_location_reference")
                        field.scroll_into_view_if_needed()
                        bounds = field.bounding_box()
                        assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
                        assert "View on Google Maps" in field.inner_text()
                    else:
                        assert page.get_by_role("heading", name="Saved station directions").is_visible()
                        assert "450 m" in page.locator(".external-sources .table-scroll table").inner_text()
                        assert "HTTP 403" in page.locator(".external-sources .table-scroll table").inner_text()
                    page.screenshot(path=str(tmp_path / f"{'project' if path == paths[0] else 'routes'}-{width}.png"), full_page=True)
            page.set_content('<h1>PR1330002502719</h1><table><tbody><tr><td>Project</td><td>Test</td></tr></tbody></table>'
                '<a href="https://maharerait.maharashtra.gov.in/public/project/view/62474">Details</a>'
                '<table hidden><tr><td>Hidden</td><td>Private</td></tr></table>')
            extracted = _extract_project_data(page)
            assert extracted["fields"] == {"Project": "Test"}
            assert len(extracted["tables"]) == 1 and extracted["links"][0]["text"] == "Details"
        finally:
            browser.close()