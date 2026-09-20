import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import (
    AuditEvent,
    FrequentPlace,
    HomeConfiguration,
    Project,
    ProjectAmenity,
    ProjectMetric,
    RecommendationItem,
    RecommendationSnapshot,
    RequirementVersion,
)
from .travel import commute_scores

MAHA_RERA_PATTERN = re.compile(r"^[A-Z][0-9A-Z]{8,19}$")
REQUIREMENT_SECTIONS = ("places", "home", "societyAmenities", "nearbyAmenities", "microMarket", "budget", "weights")
DEFAULT_WEIGHTS = {
    "commute": 0.30,
    "budget": 0.25,
    "space": 0.20,
    "societyAmenities": 0.10,
    "nearbyAmenities": 0.05,
    "microMarket": 0.05,
    "projectConfidence": 0.05,
}


def normalize_maha_rera_id(value):
    normalized = str(value or "").strip().upper()
    if not MAHA_RERA_PATTERN.fullmatch(normalized):
        raise ValidationError({"mahaReraId": [{"code": "INVALID_MAHARERA_ID", "message": "Enter a valid MahaRERA registration ID."}]})
    return normalized


def validate_requirement_payload(payload, final=False):
    if not isinstance(payload, dict):
        raise ValidationError({"sections": [{"code": "INVALID_OBJECT"}]})
    errors = {}
    if final:
        for section in REQUIREMENT_SECTIONS:
            if section not in payload:
                errors.setdefault(section, []).append({"code": "REQUIRED"})

    places = payload.get("places", [])
    if not isinstance(places, list):
        errors.setdefault("places", []).append({"code": "INVALID_LIST"})
    else:
        if len(places) > 10:
            errors.setdefault("places", []).append({"code": "TOO_MANY_PLACES"})
        seen = set()
        for index, place in enumerate(places):
            if not isinstance(place, dict):
                errors.setdefault(f"places.{index}", []).append({"code": "INVALID_OBJECT"})
                continue
            place_id = place.get("placeId")
            if (not isinstance(place_id, str) or not place_id.strip() or len(place_id) > 255
                    or any(ord(character) < 32 for character in place_id)):
                errors.setdefault(f"places.{index}.placeId", []).append({"code": "INVALID_PLACE_ID"})
            elif place_id in seen:
                errors.setdefault(f"places.{index}.placeId", []).append({"code": "DUPLICATE"})
            else:
                seen.add(place_id)
            precision = place.get("precision", "locality")
            if precision not in ("exact", "locality", "municipality"):
                errors.setdefault(f"places.{index}.precision", []).append({"code": "INVALID_PRECISION"})
            frequency = place.get("frequencyPerWeek", 0)
            if not isinstance(frequency, int) or not 0 <= frequency <= 28:
                errors.setdefault(f"places.{index}.frequencyPerWeek", []).append({"code": "OUT_OF_RANGE"})
            tolerance = place.get("maxOneWayMinutes")
            if tolerance is not None and (not isinstance(tolerance, int) or not 5 <= tolerance <= 300):
                errors.setdefault(f"places.{index}.maxOneWayMinutes", []).append({"code": "OUT_OF_RANGE"})
            latitude, longitude = place.get("latitude"), place.get("longitude")
            if (latitude is None) != (longitude is None):
                errors.setdefault(f"places.{index}", []).append({"code": "INCOMPLETE_COORDINATES"})
            elif precision == "exact" and latitude is None:
                errors.setdefault(f"places.{index}", []).append({"code": "COORDINATES_REQUIRED"})
            elif latitude is not None and (
                isinstance(latitude, bool) or isinstance(longitude, bool)
                or not isinstance(latitude, (int, float))
                or not isinstance(longitude, (int, float))
                or not -90 <= latitude <= 90
                or not -180 <= longitude <= 180
                or not math.isfinite(latitude)
                or not math.isfinite(longitude)
            ):
                errors.setdefault(f"places.{index}", []).append({"code": "INVALID_COORDINATES"})

    home = payload.get("home", {})
    area = home.get("carpetAreaSqFt", {}) if isinstance(home, dict) else {}
    minimum, preferred, maximum = area.get("minimum"), area.get("preferred"), area.get("maximum")
    numeric_area = [value for value in (minimum, preferred, maximum) if value is not None]
    if any(not isinstance(value, (int, float)) or value <= 0 or value > 100000 for value in numeric_area):
        errors.setdefault("home.carpetAreaSqFt", []).append({"code": "OUT_OF_RANGE"})
    if minimum is not None and maximum is not None and minimum > maximum:
        errors.setdefault("home.carpetAreaSqFt", []).append({"code": "INVALID_RANGE"})
    if preferred is not None and minimum is not None and preferred < minimum:
        errors.setdefault("home.carpetAreaSqFt.preferred", []).append({"code": "BELOW_MINIMUM"})

    budget = payload.get("budget", {})
    if isinstance(budget, dict):
        values = [budget.get(name) for name in ("purchaseMinimum", "purchaseMaximum", "allInMaximum") if budget.get(name) is not None]
        if any(not isinstance(value, (int, float)) or value < 0 for value in values):
            errors.setdefault("budget", []).append({"code": "OUT_OF_RANGE"})
        if budget.get("currency", "INR") != "INR":
            errors.setdefault("budget.currency", []).append({"code": "UNSUPPORTED_CURRENCY"})
        if budget.get("purchaseMinimum") is not None and budget.get("purchaseMaximum") is not None and budget["purchaseMinimum"] > budget["purchaseMaximum"]:
            errors.setdefault("budget", []).append({"code": "INVALID_RANGE"})

    weights = payload.get("weights", {})
    if isinstance(weights, dict):
        valid_weights = all(isinstance(value, (int, float)) and not isinstance(value, bool)
                            and math.isfinite(value) and 0 <= value <= 1 for value in weights.values())
        if not valid_weights:
            errors.setdefault("weights", []).append({"code": "OUT_OF_RANGE"})
        elif weights:
            total = sum(weights.values())
            if math.isclose(total, 0, rel_tol=0, abs_tol=1e-9):
                errors.setdefault("weights", []).append({"code": "ZERO_TOTAL"})
            elif total > 1 and not math.isclose(total, 1, rel_tol=0, abs_tol=1e-9):
                errors.setdefault("weights", []).append({"code": "TOTAL_EXCEEDED"})

    if errors:
        raise ValidationError(errors)
    return payload


def completion_for(payload):
    present = sum(bool(payload.get(section)) for section in REQUIREMENT_SECTIONS)
    invalid = []
    try:
        validate_requirement_payload(payload)
    except ValidationError as exc:
        invalid = list(exc.detail.keys())
    return {"percent": round(100 * present / len(REQUIREMENT_SECTIONS)), "invalidSections": invalid}


def project_frequent_places(requirement_version):
    places = []
    for item in requirement_version.payload.get("places", []):
        places.append(FrequentPlace(
            requirement_version=requirement_version,
            provider_place_id=item["placeId"],
            display_locality=str(item.get("label", ""))[:255],
            precision=item.get("precision", "locality"),
            frequency=item.get("frequencyPerWeek", 1),
            modes=item.get("modes", []),
            arrival_window=item.get("arrivalWindow", {}),
            tolerance_minutes=item.get("maxOneWayMinutes"),
        ))
    FrequentPlace.objects.bulk_create(places)


def audit(request, action, instance=None, reason_code="", metadata=None):
    correlation_id = getattr(request, "correlation_id", None)
    if correlation_id is None:
        return
    AuditEvent.objects.create(
        actor=request.user if request.user.is_authenticated else None,
        action=action,
        entity_type=instance.__class__.__name__ if instance else "system",
        entity_id=getattr(instance, "id", None),
        reason_code=reason_code,
        correlation_id=correlation_id,
        metadata=metadata or {},
    )


def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ScoringConfiguration:
    configuration_type: str
    carpet_area_max: Decimal | None
    bathrooms: int | None


def _project_configuration_data(project):
    from .layout_configurations import project_layout_configurations

    layouts = project_layout_configurations(project, can_review=False)
    if layouts:
        return [
            ScoringConfiguration(
                configuration_type=layout["configuration_type"],
                carpet_area_max=layout["carpet_area_max"],
                bathrooms=layout["bathrooms"],
            )
            for layout in layouts
        ]
    return list(HomeConfiguration.objects.filter(
        building__project=project,
        fact_set_version__lte=project.published_fact_set_version,
    ))


def _hard_constraint_failure(project, profile, configurations):
    from .layout_pricing import project_price_range

    home = profile.get("home", {})
    budget = profile.get("budget", {})
    requested_types = set(home.get("configurations", []))
    if requested_types and not any(item.configuration_type in requested_types for item in configurations):
        return "CONFIGURATION_UNAVAILABLE"
    area_min = _decimal(home.get("carpetAreaSqFt", {}).get("minimum"))
    if area_min is not None and not any(
        item.carpet_area_max is not None and item.carpet_area_max >= area_min for item in configurations
    ):
        return "BELOW_HARD_CARPET_AREA"
    bathrooms = home.get("bathroomsMinimum")
    if bathrooms and home.get("bathroomsIsHardConstraint", True) and not any(
        item.bathrooms is not None and item.bathrooms >= bathrooms for item in configurations
    ):
        return "INSUFFICIENT_BATHROOMS"
    budget_max = _decimal(budget.get("allInMaximum") or budget.get("purchaseMaximum"))
    price, _ = project_price_range(project)
    if budget.get("isHardConstraint") and budget_max is not None and price is not None and price > budget_max:
        return "OVER_HARD_BUDGET"
    required = {item.get("code") for item in profile.get("societyAmenities", []) if item.get("importance") == "required" and not item.get("substituteAccepted")}
    available = set(ProjectAmenity.objects.filter(project=project, availability_state="available").values_list("amenity__taxonomy_code", flat=True))
    if required - available:
        return "REQUIRED_AMENITY_UNAVAILABLE"
    return None


def _space_score(profile, configurations):
    preferred = _decimal(profile.get("home", {}).get("carpetAreaSqFt", {}).get("preferred"))
    areas = [item.carpet_area_max for item in configurations if item.carpet_area_max is not None]
    if preferred is None or not areas:
        return None
    best = max(areas)
    return float(min(Decimal("1"), best / preferred))


def _budget_score(profile, project):
    from .layout_pricing import project_price_range

    maximum = _decimal(profile.get("budget", {}).get("allInMaximum") or profile.get("budget", {}).get("purchaseMaximum"))
    price, _ = project_price_range(project)
    if maximum is None or price is None:
        return None
    if price <= maximum:
        return float(max(Decimal("0.5"), Decimal("1") - (maximum - price) / maximum * Decimal("0.5")))
    return float(max(Decimal("0"), Decimal("1") - (price - maximum) / maximum))


def _amenity_score(project, preferences, nearby=False):
    if not preferences:
        return None
    amenities = ProjectAmenity.objects.filter(project=project).select_related("amenity")
    matched = 0.0
    total = 0.0
    for preference in preferences:
        weight = 2.0 if preference.get("importance") == "required" else 1.0
        total += weight
        candidate = next((item for item in amenities if item.amenity.taxonomy_code == preference.get("code")), None)
        if candidate and candidate.availability_state == "available":
            if nearby:
                max_distance = preference.get("maximumDistanceMeters")
                if max_distance is None or candidate.distance_meters is None or candidate.distance_meters <= max_distance:
                    matched += weight
            else:
                matched += weight
        elif candidate and preference.get("substituteAccepted") and candidate.availability_state == "nearby_equivalent":
            matched += weight * 0.65
    return matched / total if total else None


def _metric_score(project, code):
    metric = ProjectMetric.objects.filter(project=project, metric_code=code, valid_until__gte=timezone.now()).order_by("-calculated_at").first()
    return max(0.0, min(1.0, metric.numeric_value)) if metric else None


def compute_project_metrics(project):
    from .floor_plan_visibility import visible_plan_facts
    from .layout_pricing import pricing_layouts

    now = timezone.now()
    facts = list(visible_plan_facts(project.facts.filter(
        fact_set_version=project.published_fact_set_version, status="published"
    )))
    candidates = [fact.selected_candidate for fact in facts if fact.selected_candidate_id]
    evidence_confidence = sum(item.confidence for item in candidates) / len(candidates) if candidates else 0.5
    configurations = _project_configuration_data(project)
    metric_values = {
        "evidence_confidence": (evidence_confidence, "ratio", evidence_confidence),
        "evidence_completeness": (min(1, len({fact.field_code for fact in facts}) / 10), "ratio", evidence_confidence),
        "configuration_count": (len(configurations), "count", evidence_confidence),
    }
    priced_layouts = pricing_layouts().filter(pricing_project_id=project.pk, price__isnull=False, carpet_area_sqft__gt=0)
    rates = [float(item.price / item.carpet_area_sqft) for item in priced_layouts]
    if rates:
        metric_values["price_per_carpet_sqft"] = (min(rates), "INR/sqft", evidence_confidence)
    snapshot_hash = hashlib.sha256(json.dumps({"project": str(project.id), "factSet": project.published_fact_set_version, "facts": [(item.field_code, item.typed_value) for item in facts]}, sort_keys=True, default=str).encode()).hexdigest()
    for code, (value, unit, confidence) in metric_values.items():
        ProjectMetric.objects.create(
            project=project,
            metric_code=code,
            numeric_value=value,
            unit=unit,
            confidence=confidence,
            definition_version="metrics-1.0",
            input_snapshot_hash=snapshot_hash,
            calculated_at=now,
            valid_until=now + timedelta(days=7),
        )


def _components(project, profile, configurations, commute=None):
    return {
        "commute": commute,
        "budget": _budget_score(profile, project),
        "space": _space_score(profile, configurations),
        "societyAmenities": _amenity_score(project, profile.get("societyAmenities", [])),
        "nearbyAmenities": _amenity_score(project, profile.get("nearbyAmenities", []), nearby=True),
        "microMarket": _metric_score(project, "micro_market_quality"),
        "projectConfidence": _metric_score(project, "evidence_confidence") or 0.5,
    }


@transaction.atomic
def compute_recommendations(requirement_version, snapshot=None):
    if not isinstance(requirement_version, RequirementVersion):
        requirement_version = RequirementVersion.objects.get(pk=requirement_version)
    payload = requirement_version.payload
    weights = DEFAULT_WEIGHTS | payload.get("weights", {})
    catalog_hash = hashlib.sha256(json.dumps(list(Project.objects.filter(status="published").values_list("id", "published_fact_set_version")), default=str).encode()).hexdigest()
    if snapshot is None:
        snapshot = RecommendationSnapshot.objects.create(
            requirement_version=requirement_version,
            algorithm_version="match-1.0",
            input_catalog_version=catalog_hash,
            state="computing",
        )
    else:
        snapshot = RecommendationSnapshot.objects.select_for_update().get(pk=snapshot.pk)
        if snapshot.requirement_version_id != requirement_version.id:
            raise ValueError("Recommendation snapshot does not belong to this requirement version.")
        snapshot.items.all().delete()
        snapshot.algorithm_version = "match-1.0"
        snapshot.input_catalog_version = catalog_hash
        snapshot.state = "computing"
        snapshot.save(update_fields=["algorithm_version", "input_catalog_version", "state", "updated_at"])
    scored = []
    exclusions = Counter()
    projects = list(Project.objects.filter(status="published").prefetch_related("metrics", "project_amenities__amenity"))
    commute = commute_scores(requirement_version, projects)
    for project in projects:
        configurations = _project_configuration_data(project)
        failure = _hard_constraint_failure(project, payload, configurations)
        if failure:
            exclusions[failure] += 1
            continue
        project_commute = commute.get(str(project.id))
        component_values = _components(project, payload, configurations, project_commute["score"] if project_commute else None)
        available = {key: value for key, value in component_values.items() if value is not None and weights.get(key, 0) > 0}
        denominator = sum(weights[key] for key in available)
        quality = component_values["projectConfidence"]
        score = 100 * sum(weights[key] * value for key, value in available.items()) / denominator * quality if denominator else 0
        component_dto = []
        for key, value in component_values.items():
            component = {"code": key.upper(), "weight": weights.get(key, 0), "status": "available" if value is not None else "unavailable"}
            if value is not None:
                component["score"] = value
            if key == "commute" and project_commute:
                component["destinations"] = project_commute["details"]
            component_dto.append(component)
        reasons = ["ALL_HARD_CONSTRAINTS_MET"]
        if component_values.get("commute") is not None and component_values["commute"] >= 0.7:
            reasons.append("GOOD_WEIGHTED_COMMUTE")
        scored.append((score, quality, project, component_dto, reasons))
    scored.sort(key=lambda item: (-item[0], str(item[2].id)))
    RecommendationItem.objects.bulk_create([
        RecommendationItem(snapshot=snapshot, rank=index, score=round(score, 2), confidence=quality, project=project, components=components, reason_codes=reasons)
        for index, (score, quality, project, components, reasons) in enumerate(scored, start=1)
    ])
    snapshot.state = "ready"
    snapshot.generated_at = timezone.now()
    snapshot.expires_at = timezone.now() + timedelta(hours=24)
    snapshot.excluded_summary = [{"reasonCode": key, "count": value} for key, value in sorted(exclusions.items())]
    snapshot.save(update_fields=["state", "generated_at", "expires_at", "excluded_summary", "updated_at"])
    return snapshot
