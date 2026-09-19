from unittest.mock import Mock

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from platform_api import commute_locations


SEARCH_URL = "/api/v1/commute-locations/search"
RESOLVE_URL = "/api/v1/commute-locations/resolve"


@pytest.fixture
def authenticated_client(db):
    user = User.objects.create_user(username="commute-buyer")
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_commute_lookups_require_authentication_and_session_csrf():
    assert APIClient().post(RESOLVE_URL, {"latitude": 19.1, "longitude": 72.9}, format="json").status_code == 403

    user = User.objects.create_user(username="session-buyer")
    browser = APIClient(enforce_csrf_checks=True)
    browser.force_login(user)
    assert browser.post(RESOLVE_URL, {"latitude": 19.1, "longitude": 72.9}, format="json").status_code == 403


def test_search_maps_provider_results_without_exposing_provider_metadata(
    authenticated_client, settings, monkeypatch,
):
    settings.GOOGLE_MAPS_API_KEY = "configured-for-test"
    provider = Mock(return_value=[{
        "placeId": "place-1",
        "name": "Office",
        "address": "BKC, Mumbai",
        "location": {"latitude": 19.067, "longitude": 72.869},
        "sourceUrl": "https://example.invalid/private",
        "types": ["office"],
    }])
    monkeypatch.setattr(commute_locations.GoogleProjectMaps, "search", provider)

    response = authenticated_client.post(SEARCH_URL, {"query": "BKC office"}, format="json")

    assert response.status_code == 200
    assert response["Cache-Control"] == "private, no-store"
    assert response.json() == {"results": [{
        "placeId": "place-1",
        "label": "Office, BKC, Mumbai",
        "latitude": 19.067,
        "longitude": 72.869,
        "precision": "exact",
    }]}
    provider.assert_called_once_with("BKC office")


def test_search_requires_server_side_google_configuration(authenticated_client, settings, monkeypatch):
    settings.GOOGLE_MAPS_API_KEY = ""
    provider = Mock()
    monkeypatch.setattr(commute_locations.GoogleProjectMaps, "search", provider)

    response = authenticated_client.post(SEARCH_URL, {"query": "8G7X+5W Mumbai"}, format="json")

    assert response.status_code == 503
    assert response["Cache-Control"] == "private, no-store"
    assert response.json()["code"] == "GOOGLE_MAPS_NOT_CONFIGURED"
    provider.assert_not_called()


@pytest.mark.parametrize("payload", [
    {"latitude": 19.1136, "longitude": 72.8697},
    {"mapsUrl": "https://www.google.com/maps/search/?api=1&query=19.1136%2C72.8697"},
])
def test_resolve_accepts_coordinates_and_full_google_pin_links(authenticated_client, payload):
    response = authenticated_client.post(RESOLVE_URL, payload, format="json")

    assert response.status_code == 200
    assert response["Cache-Control"] == "private, no-store"
    result = response.json()
    assert result["placeId"].startswith("private-")
    assert result["label"] == "Private commute destination"
    assert result["latitude"] == pytest.approx(19.1136)
    assert result["longitude"] == pytest.approx(72.8697)
    assert result["precision"] == "exact"


@pytest.mark.parametrize(("payload", "code"), [
    ({"latitude": 19.1}, "INVALID_COORDINATES"),
    ({"latitude": 91, "longitude": 72.9}, "INVALID_COORDINATES"),
    ({"latitude": 19.1, "longitude": 72.9, "mapsUrl": "https://maps.google.com/"}, "INVALID_LOCATION"),
    ({"mapsUrl": "http://www.google.com/maps/search/?api=1&query=19.1%2C72.9"}, "INVALID_MAPS_LOCATION"),
])
def test_resolve_rejects_invalid_or_ambiguous_input(authenticated_client, payload, code):
    response = authenticated_client.post(RESOLVE_URL, payload, format="json")

    assert response.status_code == 400
    assert response["Content-Type"].startswith("application/problem+json")
    assert response.json()["code"] == code
