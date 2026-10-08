import pytest
from rest_framework.test import APIClient

from platform_api.models import (
    Building, HomeConfiguration, Operation, Organization, Project,
    RecommendationSnapshot, RequirementProfile, TravelEstimate,
)
from platform_api.services import score_recommendations

pytestmark = pytest.mark.django_db


def requirements():
    return {
        "places": [{"placeId": "office", "label": "Work", "precision": "exact", "latitude": 19.2,
                    "longitude": 72.9, "frequencyPerWeek": 5, "modes": ["car"], "maxOneWayMinutes": 60}],
        "home": {"configurations": ["2BHK"], "carpetAreaSqFt": {"preferred": 800},
                 "bathroomsMinimum": 2, "bathroomsIsHardConstraint": True},
        "societyAmenities": [], "nearbyAmenities": [], "microMarket": {"preferredLocalities": []},
        "budget": {"currency": "INR", "purchaseMaximum": 20000000, "isHardConstraint": True},
        "weights": {"commute": .3, "budget": .25, "space": .2, "societyAmenities": .1,
                    "nearbyAmenities": .05, "microMarket": .05, "projectConfidence": .05},
        "additionalNeeds": "",
    }


def fixture_project():
    organization = Organization.objects.create(legal_name="Guest fixture")
    project = Project.objects.create(organization=organization, submitted_name="Guest home",
        maha_rera_id="P123456784", status="published", published_fact_set_version=1,
        latitude=19.15, longitude=72.88)
    building = Building.objects.create(project=project, name="A")
    HomeConfiguration.objects.create(building=building, configuration_type="2BHK",
        carpet_area_min=700, carpet_area_max=850, price_min=15000000, price_max=18000000,
        bathrooms=2, fact_set_version=1)
    return project


def test_guest_matches_shared_scoring_without_persisting_buyer_or_routes(settings):
    settings.ROOT_URLCONF = "buyer_service.urls"
    settings.ROUTING_PROVIDER = "local_approximate"
    settings.DEBUG = True
    fixture_project()
    payload = requirements()
    response = APIClient().post("/api/v1/guest/recommendations", {"sections": payload, "consent": True}, format="json")
    assert response.status_code == 200, response.content
    report = response.json()
    assert report["status"] == "ready"
    assert report["algorithmVersion"] == "match-1.0"
    assert report["items"][0]["components"][0]["status"] == "available"
    _, signed_items, exclusions = score_recommendations(payload, persist_routes=False)
    assert report["items"][0]["score"] == signed_items[0]["score"]
    assert report["excludedSummary"] == exclusions
    assert not RequirementProfile.objects.exists()
    assert not RecommendationSnapshot.objects.exists()
    assert not Operation.objects.exists()
    assert not TravelEstimate.objects.exists()


def test_guest_validation_and_unavailable_routing(settings):
    settings.ROOT_URLCONF = "buyer_service.urls"
    settings.ROUTING_PROVIDER = "unavailable"
    fixture_project()
    client = APIClient()
    payload = requirements()
    assert client.post("/api/v1/guest/recommendations", {"sections": payload}, format="json").status_code == 400
    invalid = requirements()
    invalid["weights"]["budget"] = 2
    assert client.post("/api/v1/guest/recommendations", {"sections": invalid, "consent": True}, format="json").status_code == 400
    malformed = requirements()
    malformed["home"] = []
    assert client.post("/api/v1/guest/recommendations", {"sections": malformed, "consent": True}, format="json").status_code == 400
    response = client.post("/api/v1/guest/recommendations", {"sections": payload, "consent": True}, format="json")
    assert response.status_code == 200
    assert response.json()["items"][0]["components"][0]["status"] == "unavailable"
    assert not TravelEstimate.objects.exists()


def test_guest_post_requires_csrf(settings):
    settings.ROOT_URLCONF = "buyer_service.urls"
    client = APIClient(enforce_csrf_checks=True)
    payload = {"sections": requirements(), "consent": True}
    assert client.post("/api/v1/guest/recommendations", payload, format="json").status_code == 403
    client.get("/api/v1/auth/login")
    token = client.cookies["csrftoken"].value
    assert client.post("/api/v1/guest/recommendations", payload, format="json", HTTP_X_CSRFTOKEN=token).status_code == 200
