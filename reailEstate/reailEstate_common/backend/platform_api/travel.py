import hashlib
import json
import math
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .integrations import ProviderUnavailable, RouteResult, UnavailableRoutingProvider
from .models import RequirementVersion, TravelEstimate


MODE_SPEED_KPH = {
    "walking": 4.5,
    "cycling": 13.0,
    "car": 24.0,
    "transit": 20.0,
}
MODE_ROUTE_FACTOR = {
    "walking": 1.18,
    "cycling": 1.22,
    "car": 1.35,
    "transit": 1.45,
}


class ApproximateLocalRoutingProvider:
    """Development-only, straight-line routing approximation with explicit assumptions."""

    code = "local-approximate-1.0"

    def estimate(self, origin, destination, mode, departure_time=None):
        if mode not in MODE_SPEED_KPH:
            raise ProviderUnavailable("Unsupported travel mode.")
        lat1, lon1 = _coordinates(origin)
        lat2, lon2 = _coordinates(destination)
        distance = _haversine_meters(lat1, lon1, lat2, lon2)
        routed_distance = round(distance * MODE_ROUTE_FACTOR[mode])
        duration = max(60, round(routed_distance / (MODE_SPEED_KPH[mode] * 1000 / 3600)))
        return RouteResult(
            duration_seconds=duration,
            distance_meters=routed_distance,
            retrieved_at=timezone.now(),
            assumptions={
                "kind": "straight_line_approximation",
                "trafficIncluded": False,
                "routeFactor": MODE_ROUTE_FACTOR[mode],
                "speedKph": MODE_SPEED_KPH[mode],
                "disclaimer": "Planning estimate only; not a live or predicted route.",
            },
        )


def get_routing_provider():
    provider = getattr(settings, "ROUTING_PROVIDER", "unavailable")
    if provider == "local_approximate" and settings.DEBUG:
        return ApproximateLocalRoutingProvider()
    return UnavailableRoutingProvider()


def estimate_matrix(origins, destinations, modes, departure_bucket="any", provider=None):
    provider = provider or get_routing_provider()
    provider_code = getattr(provider, "code", provider.__class__.__name__)
    results = []
    cache_hits = 0
    misses = 0
    for origin in origins:
        for destination in destinations:
            for mode in modes:
                origin_key = _location_key(origin)
                destination_key = _location_key(destination)
                cached = TravelEstimate.objects.filter(
                    origin_key=origin_key,
                    destination_key=destination_key,
                    mode=mode,
                    departure_bucket=departure_bucket,
                    provider=provider_code,
                    expires_at__gt=timezone.now(),
                ).first()
                if cached:
                    cache_hits += 1
                    results.append(_estimate_dto(cached, True))
                    continue
                misses += 1
                try:
                    route = provider.estimate(origin, destination, mode, None)
                except ProviderUnavailable:
                    results.append({
                        "originKey": origin_key,
                        "destinationKey": destination_key,
                        "mode": mode,
                        "status": "unavailable",
                    })
                    continue
                estimate, _ = TravelEstimate.objects.update_or_create(
                    origin_key=origin_key,
                    destination_key=destination_key,
                    mode=mode,
                    departure_bucket=departure_bucket,
                    provider=provider_code,
                    defaults={
                        "duration_seconds": route.duration_seconds,
                        "distance_meters": route.distance_meters,
                        "assumptions": route.assumptions,
                        "retrieved_at": route.retrieved_at,
                        "expires_at": route.retrieved_at + timedelta(seconds=settings.ROUTING_CACHE_SECONDS),
                    },
                )
                results.append(_estimate_dto(estimate, False))
    available = sum(item["status"] == "available" for item in results)
    if available == len(results):
        matrix_status = "available"
    elif available:
        matrix_status = "partially_available"
    else:
        matrix_status = "unavailable"
    return {
        "status": matrix_status,
        "provider": provider_code,
        "estimates": results,
        "cacheHits": cache_hits,
        "misses": misses,
    }


def commute_scores(requirement_version, projects, provider=None):
    if not isinstance(requirement_version, RequirementVersion):
        requirement_version = RequirementVersion.objects.get(pk=requirement_version)
    places = requirement_version.payload.get("places", [])
    routable_places = [place for place in places if _has_coordinates(place)]
    if not routable_places:
        return {}
    scores = {}
    for project in projects:
        if project.latitude is None or project.longitude is None:
            continue
        destination = {
            "placeId": f"project:{project.id}",
            "latitude": float(project.latitude),
            "longitude": float(project.longitude),
        }
        weighted_score = 0.0
        total_frequency = 0
        details = []
        for place in routable_places:
            modes = place.get("modes") or ["transit"]
            matrix = estimate_matrix([place], [destination], modes, _arrival_bucket(place), provider)
            available = [item for item in matrix["estimates"] if item["status"] == "available"]
            if not available:
                details.append({"placeId": place.get("placeId"), "status": "unavailable"})
                continue
            best = min(available, key=lambda item: item["durationSeconds"])
            frequency = max(0, int(place.get("frequencyPerWeek", 0)))
            tolerance = max(1, int(place.get("maxOneWayMinutes") or settings.DEFAULT_COMMUTE_TOLERANCE_MINUTES))
            destination_score = max(0.0, 1.0 - best["durationSeconds"] / 60 / tolerance)
            weighted_score += frequency * destination_score
            total_frequency += frequency
            details.append({
                "placeId": place.get("placeId"),
                "status": "available",
                "mode": best["mode"],
                "durationMinutes": round(best["durationSeconds"] / 60, 1),
                "distanceMeters": best["distanceMeters"],
                "toleranceMinutes": tolerance,
                "frequencyPerWeek": frequency,
                "score": round(destination_score, 4),
                "assumptions": best["assumptions"],
                "retrievedAt": best["retrievedAt"],
            })
        if total_frequency:
            scores[str(project.id)] = {"score": weighted_score / total_frequency, "details": details}
    return scores


def _coordinates(location):
    latitude = location.get("latitude")
    longitude = location.get("longitude")
    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        raise ProviderUnavailable("Coordinates are required by the local routing provider.")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ProviderUnavailable("Coordinates are outside valid bounds.")
    return float(latitude), float(longitude)


def _has_coordinates(location):
    return isinstance(location, dict) and isinstance(location.get("latitude"), (int, float)) and isinstance(location.get("longitude"), (int, float))


def _location_key(location):
    if location.get("placeId"):
        return str(location["placeId"])[:128]
    latitude, longitude = _coordinates(location)
    precision = 3 if location.get("precision") in {"locality", "municipality"} else 5
    normalized = json.dumps([round(latitude, precision), round(longitude, precision)], separators=(",", ":"))
    return f"geo:{hashlib.sha256(normalized.encode()).hexdigest()[:32]}"


def _arrival_bucket(place):
    window = place.get("arrivalWindow") or {}
    return f"{window.get('start', 'any')}-{window.get('end', 'any')}@{window.get('timeZone', 'Asia/Kolkata')}"[:64]


def _haversine_meters(lat1, lon1, lat2, lon2):
    radius = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    value = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def _estimate_dto(estimate, cache_hit):
    return {
        "originKey": estimate.origin_key,
        "destinationKey": estimate.destination_key,
        "mode": estimate.mode,
        "status": "available",
        "durationSeconds": estimate.duration_seconds,
        "distanceMeters": estimate.distance_meters,
        "retrievedAt": estimate.retrieved_at,
        "expiresAt": estimate.expires_at,
        "assumptions": estimate.assumptions,
        "cacheHit": cache_hit,
    }
