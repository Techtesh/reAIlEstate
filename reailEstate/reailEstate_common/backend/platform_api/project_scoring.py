"""Project-level requirements and scores shared by every matching layout."""

from django.utils import timezone


MAX_NEARBY_AMENITY_DISTANCE_METERS = 5_000


def _amenity_score(amenities, preferences, nearby=False):
    if not preferences:
        return None
    matched = total = 0.0
    for preference in preferences:
        weight = 2.0 if preference.get("importance") == "required" else 1.0
        total += weight
        candidate = next((item for item in amenities if item.amenity.taxonomy_code == preference.get("code")), None)
        if candidate and candidate.availability_state == "available":
            if nearby:
                maximum = min(preference.get("maximumDistanceMeters") or MAX_NEARBY_AMENITY_DISTANCE_METERS, MAX_NEARBY_AMENITY_DISTANCE_METERS)
                if candidate.distance_meters is not None and candidate.distance_meters <= maximum:
                    matched += weight
            else:
                matched += weight
        elif (candidate and preference.get("substituteAccepted") and candidate.availability_state == "nearby_equivalent"
              and candidate.distance_meters is not None and candidate.distance_meters <= MAX_NEARBY_AMENITY_DISTANCE_METERS):
            matched += weight * 0.65
    return matched / total if total else None


def score_project(project, profile):
    amenities = list(project.project_amenities.all())
    required = {item.get("code") for item in profile.get("societyAmenities", [])
                if item.get("importance") == "required" and not item.get("substituteAccepted")}
    available = {item.amenity.taxonomy_code for item in amenities if item.availability_state == "available"}
    if required - available:
        return {}, "REQUIRED_AMENITY_UNAVAILABLE"
    metrics = {}
    now = timezone.now()
    for metric in sorted(project.metrics.all(), key=lambda item: item.calculated_at, reverse=True):
        if metric.valid_until is not None and metric.valid_until >= now and metric.metric_code not in metrics:
            metrics[metric.metric_code] = max(0.0, min(1.0, metric.numeric_value))
    return {
        "commute": None,
        "societyAmenities": _amenity_score(amenities, profile.get("societyAmenities", [])),
        "nearbyAmenities": _amenity_score(amenities, profile.get("nearbyAmenities", []), nearby=True),
        "microMarket": metrics.get("micro_market_quality"),
        "projectConfidence": metrics.get("evidence_confidence") or 0.5,
    }, None
