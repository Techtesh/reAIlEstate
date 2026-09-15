import pytest
from django.test import override_settings

from platform_api.models import TravelEstimate
from platform_api.travel import ApproximateLocalRoutingProvider, estimate_matrix


ORIGIN = {"placeId": "place:home", "latitude": 19.076, "longitude": 72.8777, "precision": "locality"}
DESTINATION = {"placeId": "project:test", "latitude": 19.1176, "longitude": 72.906}


@pytest.mark.django_db
@override_settings(ROUTING_PROVIDER="local_approximate", ROUTING_CACHE_SECONDS=3600)
def test_local_travel_preview_is_cached():
    first = estimate_matrix([ORIGIN], [DESTINATION], ["transit"])
    second = estimate_matrix([ORIGIN], [DESTINATION], ["transit"])

    assert first["status"] == "available"
    assert first["cacheHits"] == 0
    assert second["cacheHits"] == 1
    assert second["estimates"][0]["assumptions"]["trafficIncluded"] is False
    assert TravelEstimate.objects.count() == 1


def test_local_provider_rejects_missing_coordinates():
    provider = ApproximateLocalRoutingProvider()
    with pytest.raises(Exception):
        provider.estimate({"placeId": "missing"}, DESTINATION, "car")
