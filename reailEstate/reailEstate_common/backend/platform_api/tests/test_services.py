from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from rest_framework.exceptions import ValidationError

from platform_api.integrations import validate_provider_url
from platform_api.services import (
    _project_configuration_data,
    _space_score,
    completion_for,
    normalize_maha_rera_id,
    validate_requirement_payload,
)


def test_maha_rera_id_is_normalized():
    assert normalize_maha_rera_id(" p123456789 ") == "P123456789"


def test_invalid_requirement_ranges_are_rejected():
    with pytest.raises(ValidationError):
        validate_requirement_payload({"home": {"carpetAreaSqFt": {"minimum": 900, "maximum": 600}}})


def test_duplicate_places_are_rejected():
    place = {"placeId": "opaque", "frequencyPerWeek": 5}
    with pytest.raises(ValidationError):
        validate_requirement_payload({"places": [place, place]})


def test_requirement_weights_must_be_finite_and_within_budget():
    validate_requirement_payload({"weights": {"space": .8, "budget": .2}})
    validate_requirement_payload({"weights": {"space": .8}})
    for weights, code in (
        ({"space": .8, "budget": .3}, "TOTAL_EXCEEDED"),
        ({"space": 0, "budget": 0}, "ZERO_TOTAL"),
        ({"space": "1"}, "OUT_OF_RANGE"),
        ({"space": float("nan")}, "OUT_OF_RANGE"),
        ({"space": True}, "OUT_OF_RANGE"),
    ):
        with pytest.raises(ValidationError) as error:
            validate_requirement_payload({"weights": weights})
        assert error.value.detail["weights"][0]["code"] == code


def test_scoring_uses_public_layout_configurations():
    layouts = [{
        "configuration_type": "2BHK",
        "carpet_area_max": Decimal("750"),
        "bathrooms": None,
    }]
    with patch("platform_api.layout_configurations.project_layout_configurations", return_value=layouts):
        configurations = _project_configuration_data(Mock())

    assert configurations[0].configuration_type == "2BHK"
    assert configurations[0].carpet_area_max == Decimal("750")
    assert _space_score({"home": {"carpetAreaSqFt": {"preferred": 800}}}, configurations) == .9375
    assert _space_score({"home": {"carpetAreaSqFt": {"preferred": 800}}}, [
        type(configurations[0])("2BHK", None, None),
    ]) is None


def test_completion_uses_canonical_sections():
    completion = completion_for({"places": [{"placeId": "one", "frequencyPerWeek": 1}]})
    assert completion["percent"] > 0


def test_provider_url_requires_exact_https_allowlist():
    assert validate_provider_url("https://api.example.test/path", {"api.example.test"}) == "https://api.example.test/path"
    with pytest.raises(Exception):
        validate_provider_url("http://api.example.test/path", {"api.example.test"})
