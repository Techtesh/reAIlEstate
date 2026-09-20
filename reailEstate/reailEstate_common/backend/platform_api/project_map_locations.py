from django.conf import settings
from django.db.models import Q

from .external_sources import coordinates
from .builder_locations import BUILDER_LOCATION_SOURCE
from .models import ReviewTask


def _location(value):
    try:
        return coordinates(value)
    except (TypeError, ValueError):
        return None


def project_map_location(project):
    unavailable = {"latitude": None, "longitude": None, "location_status": "unavailable"}
    if project.status not in {"published", "ingestion", "needs_review"}:
        return unavailable
    stored = _location({
        "latitude": float(project.latitude) if project.latitude is not None else None,
        "longitude": float(project.longitude) if project.longitude is not None else None,
    })
    if project.status == "published" and stored:
        return {**stored, "location_status": "published"}
    preview = settings.PROJECT_PUBLIC_PREVIEW_ENABLED
    if not stored and not preview:
        return unavailable
    reference = ReviewTask.objects.filter(project=project, task_type="external_sources").filter(
        Q(resolution__manualLocation__isnull=False) | Q(resolution__googlePlaceId__isnull=False)
    ).exclude(
        Q(resolution__source=BUILDER_LOCATION_SOURCE) & ~Q(resolution__reviewState="accepted")
    ).order_by("-created_at", "-pk").first()
    if not reference:
        return unavailable
    resolution = reference.resolution
    accepted = reference.state == "resolved" and resolution.get("reviewState") == "accepted"
    if stored and accepted:
        return {**stored, "location_status": "reviewed"}
    if not preview or resolution.get("reviewState") not in {"unreviewed", "accepted"}:
        return unavailable
    manual = resolution.get("manualLocation")
    candidate = _location(manual.get("location")) if isinstance(manual, dict) else None
    routes = resolution.get("stationRoutes")
    if not candidate and isinstance(routes, dict) and resolution.get("googlePlaceId"):
        if routes.get("originPlaceId") == resolution["googlePlaceId"]:
            candidate = _location(routes.get("origin"))
    if not candidate:
        return unavailable
    # Preview never applies coordinates or changes the location review state.
    return {**candidate, "location_status": "provisional"}
