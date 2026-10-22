from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.db import DatabaseError
from django.utils import timezone
from rest_framework.test import APIClient

from platform_api.feature_flags import REGISTRY
from platform_api.feature_flags.validation import FlagValidationError, validate_configuration
from platform_api.models import (
    Amenity, Building, FeatureFlag, FloorPlan, HomeConfiguration, Project, ProjectAmenity,
    ProjectMerge, ProjectMetric, RecommendationSnapshot, RequirementProfile, RequirementVersion,
)
from platform_api.services import compute_recommendations, score_recommendations

from .test_guest_recommendations import requirements
from .test_layout_pricing import another, layout, source

pytestmark = pytest.mark.django_db
FLAG = "recommendations.layout_hard_matches"


@pytest.fixture
def home(layout, settings):
    settings.ENVIRONMENT = "development"
    settings.ROUTING_PROVIDER = "unavailable"
    plan, _ = layout
    plan.configuration_type, plan.carpet_area_sqft, plan.bathroom_count = "2BHK", 750, 2
    plan.save()
    return plan


def strict():
    return FeatureFlag.objects.create(key=FLAG, environment="development", enabled=True, version=3)


def profile():
    result = requirements()
    result["places"] = []
    result["home"]["carpetAreaSqFt"]["minimum"] = 700
    return result


def test_layout_flag_defaults_off_and_cannot_target_users(home):
    assert REGISTRY[FLAG].default_enabled is False
    assert REGISTRY[FLAG].targetable is False
    assert score_recommendations(profile()).matching_context == {"layoutHardMatches": False, "featureFlagVersion": None}
    assert not FeatureFlag.objects.exists()
    with pytest.raises(FlagValidationError):
        validate_configuration(FLAG, True, {"user_ids": ["1"]})
    with patch("platform_api.feature_flags.evaluator.FeatureFlag.objects.filter", side_effect=DatabaseError):
        assert score_recommendations(profile()).matching_context["layoutHardMatches"] is False


def test_lenient_scores_the_requested_home_and_exposes_violations(home):
    home.carpet_area_sqft, home.bathroom_count, home.price = 650, 1, 25000000
    home.save()
    another(home, configuration_type="3BHK", carpet_area_sqft=900, bathroom_count=3, price=10000000)
    result = score_recommendations(profile())
    assert len(result.items) == 1
    item = result.items[0]
    matched = item["layoutMatch"]
    assert matched["matchedLayoutCount"] == 1 and matched["bestLayoutId"] == str(home.pk)
    assert matched["layouts"][0]["violatedConstraints"] == ["carpetAreaSqFt", "bathrooms", "price"]
    values = {component["code"]: component.get("score") for component in item["components"]}
    assert values["SPACE"] == .8125 and values["BUDGET"] == .75
    assert item["reasonCodes"][0] == "LAYOUT_HARD_CONSTRAINTS_NOT_ENFORCED"
    strict()
    result = score_recommendations(profile())
    assert result.items == [] and result.exclusions == [{"reasonCode": "BELOW_HARD_CARPET_AREA", "count": 1}]


@pytest.mark.parametrize("enabled", [False, True])
def test_only_selected_configurations_match_in_either_mode(home, enabled):
    if enabled:
        strict()
    another(home, configuration_type="3 BHK", carpet_area_sqft=900, bathroom_count=3, price=18000000)
    another(home, configuration_type="4BHK", carpet_area_sqft=1500, bathroom_count=4, price=19000000)
    payload = profile()
    payload["home"]["configurations"] = ["2 bhk", "3BHK"]
    item = score_recommendations(payload).items[0]
    assert item["layoutMatch"]["matchedLayoutCount"] == 2
    assert {entry["configurationType"] for entry in item["layoutMatch"]["layouts"]} == {"2BHK", "3BHK"}
    payload["home"]["configurations"] = ["1RK"]
    result = score_recommendations(payload)
    assert result.items == [] and result.exclusions == [{"reasonCode": "CONFIGURATION_UNAVAILABLE", "count": 1}]


@pytest.mark.parametrize("first_changes,reason", [
    ({"carpet_area_sqft": 650}, "OVER_HARD_BUDGET"),
    ({"bathroom_count": 1}, "OVER_HARD_BUDGET"),
])
def test_strict_requirements_must_be_met_by_one_layout(home, first_changes, reason):
    strict()
    for field, value in first_changes.items():
        setattr(home, field, value)
    home.save()
    other = another(home, configuration_type="2BHK", carpet_area_sqft=900, bathroom_count=2, price=25000000)
    result = score_recommendations(profile())
    assert result.items == [] and result.exclusions == [{"reasonCode": reason, "count": 1}]
    other.price = 19000000
    other.save()
    matched = score_recommendations(profile()).items[0]["layoutMatch"]
    assert matched["bestLayoutId"] == str(other.pk) and matched["matchedLayoutCount"] == 1


@pytest.mark.parametrize("field,diagnostic,component", [
    ("carpet_area_sqft", "carpetAreaSqFt", "SPACE"),
    ("bathroom_count", "bathrooms", None),
    ("price", "price", "BUDGET"),
])
def test_missing_required_layout_data_is_advisory_only_when_flag_off(home, field, diagnostic, component):
    setattr(home, field, None)
    home.save()
    result = score_recommendations(profile())
    item = result.items[0]
    assert item["layoutMatch"]["layouts"][0]["unverifiedConstraints"] == [diagnostic]
    if component:
        assert next(value for value in item["components"] if value["code"] == component)["status"] == "unavailable"
    strict()
    result = score_recommendations(profile())
    assert result.items == [] and result.exclusions == [{"reasonCode": "LAYOUT_REQUIRED_DATA_UNAVAILABLE", "count": 1}]


def test_strict_keeps_maximum_area_non_excluding_and_respects_soft_constraints(home):
    strict()
    home.carpet_area_sqft, home.bathroom_count, home.price = 950, None, None
    home.save()
    payload = profile()
    payload["home"]["carpetAreaSqFt"]["maximum"] = 850
    payload["home"]["bathroomsIsHardConstraint"] = False
    payload["budget"]["isHardConstraint"] = False
    assert len(score_recommendations(payload).items) == 1


def test_all_in_budget_uses_matching_layout_not_cheapest_other_configuration(home):
    strict()
    home.configuration_type, home.price = "3BHK", 25000000
    home.save()
    another(home, configuration_type="2BHK", carpet_area_sqft=750, bathroom_count=2, price=10000000)
    payload = profile()
    payload["home"]["configurations"] = ["3BHK"]
    result = score_recommendations(payload)
    assert result.items == [] and result.exclusions == [{"reasonCode": "OVER_HARD_BUDGET", "count": 1}]
    payload["budget"]["allInMaximum"] = 26000000
    item = score_recommendations(payload).items[0]
    assert item["layoutMatch"]["layouts"][0]["price"] == "25000000.00"


@pytest.mark.parametrize("changes", [
    {"review_state": "needs_review"}, {"review_state": "rejected"}, {"active": False},
    {"plan_scope": "floor"}, {"plan_scope": "unknown"},
])
def test_unvalidated_layouts_never_match_or_use_legacy_fallback(home, changes, settings):
    settings.PROJECT_PUBLIC_PREVIEW_ENABLED = True
    FloorPlan.objects.filter(pk=home.pk).update(**changes)
    HomeConfiguration.objects.create(building=Building.objects.create(project=home.project, name="A"),
        configuration_type="2BHK", carpet_area_min=700, carpet_area_max=850, bathrooms=2, price_min=10000000)
    result = score_recommendations(profile())
    assert result.items == [] and result.exclusions == [{"reasonCode": "NO_VALIDATED_LAYOUT", "count": 1}]


def test_project_scores_are_shared_and_hard_amenities_still_exclude_in_lenient_mode(home):
    another(home, configuration_type="2BHK", carpet_area_sqft=850, bathroom_count=2, price=18000000)
    payload = profile()
    payload["societyAmenities"] = [{"code": "gym", "importance": "required", "substituteAccepted": False}]
    result = score_recommendations(payload)
    assert result.items == [] and result.exclusions == [{"reasonCode": "REQUIRED_AMENITY_UNAVAILABLE", "count": 1}]
    ProjectAmenity.objects.create(project=home.project, amenity=Amenity.objects.create(taxonomy_code="gym", display_name="Gym"), availability_state="available")
    for code, value in (("micro_market_quality", .7), ("evidence_confidence", .8)):
        ProjectMetric.objects.create(project=home.project, metric_code=code, numeric_value=value,
            confidence=.9, definition_version="test", input_snapshot_hash="test",
            calculated_at=timezone.now(), valid_until=timezone.now() + timedelta(days=1))
    item = score_recommendations(payload).items[0]
    values = {entry["code"]: entry.get("score") for entry in item["components"]}
    assert values["SOCIETYAMENITIES"] == 1 and values["MICROMARKET"] == .7 and values["PROJECTCONFIDENCE"] == .8
    assert item["layoutMatch"]["matchedLayoutCount"] == 2 and item["confidence"] == .8


@pytest.mark.parametrize("expiry", [None, -1])
def test_project_scores_ignore_unavailable_or_expired_metrics(home, expiry):
    ProjectMetric.objects.create(project=home.project, metric_code="micro_market_quality", numeric_value=.9,
        confidence=.9, definition_version="test", input_snapshot_hash="test", calculated_at=timezone.now(),
        valid_until=None if expiry is None else timezone.now() + timedelta(days=expiry))
    item = score_recommendations(profile()).items[0]
    assert next(value for value in item["components"] if value["code"] == "MICROMARKET")["status"] == "unavailable"


def test_layout_query_count_is_constant_and_summaries_are_bounded(home, django_assert_num_queries):
    with django_assert_num_queries(5):
        first = score_recommendations(profile())
    for index in range(4):
        another(home, configuration_type="2BHK", carpet_area_sqft=800 + index * 10, bathroom_count=2, price=18000000 + index * 100000)
    with django_assert_num_queries(5):
        result = score_recommendations(profile())
    assert result.catalog_version != first.catalog_version
    matched = result.items[0]["layoutMatch"]
    assert matched["matchedLayoutCount"] == 5 and len(matched["layouts"]) == 3
    assert matched["layouts"][0]["id"] == matched["bestLayoutId"]
    assert matched["layouts"][0]["score"] == result.items[0]["score"]
    assert [entry["score"] for entry in matched["layouts"]] == sorted([entry["score"] for entry in matched["layouts"]], reverse=True)


def test_approved_child_layouts_match_the_canonical_project_after_merge(home):
    target = Project.objects.create(organization=home.project.organization, submitted_name="Canonical", status="published")
    ProjectMerge.objects.create(source_project=home.project, target_project=target, state="applied", reason_code="TEST")
    home.project.status = "merged"
    home.project.save()
    item = score_recommendations(profile()).items[0]
    assert item["projectId"] == str(target.pk) and item["layoutMatch"]["bestLayoutId"] == str(home.pk)
    response = APIClient().get("/api/v1/projects/?configuration=2BHK&carpetAreaMinimum=700&allInPriceMaximum=20000000")
    assert [entry["id"] for entry in response.json()["results"]] == [str(target.pk)]


def test_browse_filters_require_the_same_approved_layout(home, settings):
    settings.PROJECT_PUBLIC_PREVIEW_ENABLED = True
    home.carpet_area_sqft = 650
    home.save()
    other = another(home, configuration_type="3BHK", carpet_area_sqft=900, bathroom_count=2, price=10000000)
    query = "/api/v1/projects/?configuration=2%20bhk&carpetAreaMinimum=700&allInPriceMaximum=20000000"
    client = APIClient()
    assert client.get(query).json()["results"] == []
    other.configuration_type, other.review_state = "2 BHK", "needs_review"
    other.save()
    assert client.get(query).json()["results"] == []
    other.review_state, other.price = "approved", 25000000
    other.save()
    assert client.get(query).json()["results"] == []
    other.price = 18000000
    other.save()
    assert [entry["id"] for entry in client.get(query).json()["results"]] == [str(home.project_id)]
    for parameter in ("carpetAreaMinimum=NaN", "carpetAreaMinimum=-1", "allInPriceMaximum=Infinity", "allInPriceMaximum=cheap"):
        assert client.get("/api/v1/projects/?" + parameter).status_code == 400


def test_new_snapshot_freezes_layouts_and_flag_while_completed_legacy_stays_untouched(home):
    flag = strict()
    owner = User.objects.create_user(username="layout-snapshot-owner")
    version = RequirementVersion.objects.create(profile=RequirementProfile.objects.create(owner=owner), version=1, payload=profile(), status="submitted")
    snapshot = compute_recommendations(version)
    assert snapshot.algorithm_version == "match-2.0" and snapshot.matching_context == {"layoutHardMatches": True, "featureFlagVersion": 3}
    before = deepcopy(snapshot.items.get().layout_match)
    home.price, home.display_name = 30000000, "Changed later"
    home.save()
    flag.enabled, flag.version = False, 4
    flag.save()
    assert compute_recommendations(version, snapshot=snapshot).items.get().layout_match == before
    snapshot.refresh_from_db()
    assert snapshot.matching_context["layoutHardMatches"] is True
    updated = compute_recommendations(version)
    assert updated.matching_context == {"layoutHardMatches": False, "featureFlagVersion": 4}
    assert updated.input_catalog_version != snapshot.input_catalog_version
    assert updated.items.get().layout_match["layouts"][0]["price"] == "30000000.00"
    legacy = RecommendationSnapshot.objects.create(requirement_version=version, state="ready", algorithm_version="match-1.0", input_catalog_version="legacy")
    assert compute_recommendations(version, snapshot=legacy).algorithm_version == "match-1.0"
    legacy.refresh_from_db()
    assert legacy.matching_context == {} and not legacy.items.exists()
