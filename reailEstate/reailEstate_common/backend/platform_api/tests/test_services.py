from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from rest_framework.exceptions import ValidationError

from platform_api.integrations import validate_provider_url
from platform_api.services import (
    _project_configuration_data,
    _space_score,
    completion_for,
    compute_recommendations,
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


@pytest.mark.parametrize("payload, field", [
    ({"home": []}, "home"),
    ({"home": {"carpetAreaSqFt": []}}, "home.carpetAreaSqFt"),
    ({"home": {"carpetAreaSqFt": {"minimum": "large", "maximum": 900}}}, "home.carpetAreaSqFt"),
    ({"home": {"carpetAreaSqFt": {"preferred": float("nan")}}}, "home.carpetAreaSqFt"),
    ({"home": {"carpetAreaSqFt": {"preferred": True}}}, "home.carpetAreaSqFt"),
    ({"home": {"carpetAreaSqFt": {"preferred": 900, "maximum": 800}}}, "home.carpetAreaSqFt.preferred"),
    ({"home": {"configurations": [{}]}}, "home.configurations"),
    ({"home": {"bathroomsMinimum": "two"}}, "home.bathroomsMinimum"),
    ({"budget": []}, "budget"),
    ({"budget": {"purchaseMinimum": "cheap", "purchaseMaximum": 20000000}}, "budget"),
    ({"budget": {"purchaseMaximum": 0}}, "budget"),
    ({"budget": {"allInMaximum": float("inf")}}, "budget"),
    ({"budget": {"purchaseMaximum": True}}, "budget"),
    ({"budget": {"isHardConstraint": "yes"}}, "budget.isHardConstraint"),
    ({"weights": []}, "weights"),
    ({"weights": {"space": 10**400}}, "weights"),
    ({"societyAmenities": ["gym"]}, "societyAmenities.0"),
    ({"nearbyAmenities": [{"code": "school", "maximumDistanceMeters": "near"}]}, "nearbyAmenities.0.maximumDistanceMeters"),
    ({"microMarket": []}, "microMarket"),
    ({"places": [{"placeId": "work", "modes": [{}]}]}, "places.0.modes"),
    ({"places": [{"placeId": "work", "arrivalWindow": []}]}, "places.0.arrivalWindow"),
])
def test_malformed_requirements_raise_actionable_validation(payload, field):
    with pytest.raises(ValidationError) as error:
        validate_requirement_payload(payload)
    assert error.value.detail[field][0]["code"]
    assert error.value.detail[field][0]["message"]


@pytest.mark.django_db
@pytest.mark.parametrize("state", ["ready", "partially_ready", "expired"])
def test_completed_recommendation_snapshot_is_immutable(state):
    from django.contrib.auth.models import User
    from django.utils import timezone
    from platform_api.models import InboxMessage, Operation, Organization, OutboxMessage, Project, RecommendationItem, RecommendationSnapshot, RequirementProfile, RequirementVersion
    from platform_api.workers import process_pending_outbox

    profile = RequirementProfile.objects.create(owner=User.objects.create_user(username="snapshot-owner"))
    version = RequirementVersion.objects.create(profile=profile, version=1, payload={}, status="submitted")
    snapshot = RecommendationSnapshot.objects.create(
        requirement_version=version, state=state, input_catalog_version="original-catalogue",
        algorithm_version="original-algorithm", generated_at=timezone.now(), expires_at=timezone.now(),
        excluded_summary=[{"reasonCode": "OVER_HARD_BUDGET", "count": 2}],
    )
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Snapshot fixture"), submitted_name="Original match")
    RecommendationItem.objects.create(snapshot=snapshot, project=project, rank=1, score=75, confidence=.8,
        components=[{"code": "SPACE", "score": .9}], reason_codes=["ALL_HARD_CONSTRAINTS_MET"])
    before = RecommendationSnapshot.objects.filter(pk=snapshot.pk).values().get()
    items_before = list(snapshot.items.values())
    with patch("platform_api.services.score_recommendations") as scorer:
        result = compute_recommendations(version, snapshot=snapshot)
        operation = Operation.objects.create(owner=profile.owner, kind="recommendation", state="completed")
        for _ in range(2):
            replay = OutboxMessage.objects.create(event_type="recommendation.computation.requested", correlation_id=version.id,
                payload={"operationId": str(operation.id), "requirementVersionId": str(version.id), "snapshotId": str(snapshot.id)})
            assert process_pending_outbox(message_ids=[replay.id]) == {"processed": 1, "retry": 0, "failed": 0}
            assert InboxMessage.objects.filter(message_id=replay.id).exists()
        operation.refresh_from_db()
        assert operation.state == "completed"
    scorer.assert_not_called()
    assert result.pk == snapshot.pk
    assert RecommendationSnapshot.objects.filter(pk=snapshot.pk).values().get() == before
    assert list(snapshot.items.values()) == items_before
    wrong_version = RequirementVersion.objects.create(profile=profile, version=2, payload={}, status="submitted")
    with pytest.raises(ValueError, match="does not belong"):
        compute_recommendations(wrong_version, snapshot=snapshot)


@pytest.mark.django_db
@pytest.mark.parametrize("state", ["requested", "computing", "failed"])
def test_incomplete_recommendation_snapshot_can_be_computed(state):
    from django.contrib.auth.models import User
    from platform_api.models import RecommendationSnapshot, RequirementProfile, RequirementVersion

    profile = RequirementProfile.objects.create(owner=User.objects.create_user(username="retry-owner"))
    version = RequirementVersion.objects.create(profile=profile, version=1, payload={}, status="submitted")
    snapshot = RecommendationSnapshot.objects.create(requirement_version=version, state=state, input_catalog_version="pending")
    with patch("platform_api.services.score_recommendations", return_value=("new-catalogue", [], [])) as scorer:
        result = compute_recommendations(version, snapshot=snapshot)
    scorer.assert_called_once_with({}, persist_routes=True)
    assert result.state == "ready"
    assert result.input_catalog_version == "new-catalogue"
    assert result.generated_at is not None


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
