import pytest
from rest_framework.exceptions import ValidationError

from platform_api.integrations import validate_provider_url
from platform_api.services import completion_for, normalize_maha_rera_id, validate_requirement_payload


def test_maha_rera_id_is_normalized():
    assert normalize_maha_rera_id(" p123456789 ") == "P123456789"


def test_invalid_requirement_ranges_are_rejected():
    with pytest.raises(ValidationError):
        validate_requirement_payload({"home": {"carpetAreaSqFt": {"minimum": 900, "maximum": 600}}})


def test_duplicate_places_are_rejected():
    place = {"placeId": "opaque", "frequencyPerWeek": 5}
    with pytest.raises(ValidationError):
        validate_requirement_payload({"places": [place, place]})


def test_completion_uses_canonical_sections():
    completion = completion_for({"places": [{"placeId": "one", "frequencyPerWeek": 1}]})
    assert completion["percent"] > 0


def test_provider_url_requires_exact_https_allowlist():
    assert validate_provider_url("https://api.example.test/path", {"api.example.test"}) == "https://api.example.test/path"
    with pytest.raises(Exception):
        validate_provider_url("http://api.example.test/path", {"api.example.test"})
