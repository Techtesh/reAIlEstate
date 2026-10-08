import pytest
from rest_framework.test import APIClient

from platform_api.models import (
    Operation,
    RecommendationSnapshot, RequirementProfile, TravelEstimate,
)
from platform_api.services import score_recommendations
from .test_layout_pricing import layout, source

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


def fixture_project(layout):
    plan, _ = layout
    plan.project.latitude, plan.project.longitude = 19.15, 72.88
    plan.project.save()
    plan.configuration_type, plan.carpet_area_sqft, plan.bathroom_count = "2BHK", 850, 2
    plan.save()
    return plan.project


def test_guest_matches_shared_scoring_without_persisting_buyer_or_routes(settings, layout):
    settings.ROOT_URLCONF = "buyer_service.urls"
    settings.ROUTING_PROVIDER = "local_approximate"
    settings.DEBUG = True
    fixture_project(layout)
    payload = requirements()
    response = APIClient().post("/api/v1/guest/recommendations", {"sections": payload, "consent": True}, format="json")
    assert response.status_code == 200, response.content
    report = response.json()
    assert report["status"] == "ready"
    assert report["algorithmVersion"] == "match-2.0"
    assert report["matchingContext"]["layoutHardMatches"] is False
    assert report["items"][0]["layoutMatch"]["bestLayoutId"] == str(layout[0].pk)
    assert report["items"][0]["components"][0]["status"] == "available"
    result = score_recommendations(payload, persist_routes=False)
    assert report["items"][0]["score"] == result.items[0]["score"]
    assert report["excludedSummary"] == result.exclusions
    assert not RequirementProfile.objects.exists()
    assert not RecommendationSnapshot.objects.exists()
    assert not Operation.objects.exists()
    assert not TravelEstimate.objects.exists()


def test_guest_validation_and_unavailable_routing(settings, layout):
    settings.ROOT_URLCONF = "buyer_service.urls"
    settings.ROUTING_PROVIDER = "unavailable"
    fixture_project(layout)
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
