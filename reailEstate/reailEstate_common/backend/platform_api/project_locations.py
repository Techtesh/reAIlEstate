"""Project location proposals use the existing independent location review workflow."""

from django.db.models import Q
from rest_framework.exceptions import ValidationError

from .external_sources import coordinates, direct_location


PROJECT_LOCATION_SOURCE = "project_workspace"


def project_location_data(project):
    reference = project.review_tasks.filter(task_type="external_sources").filter(
        Q(resolution__manualLocation__isnull=False) | Q(resolution__googlePlaceId__isnull=False)
    ).order_by("-created_at", "-pk").first()
    if reference and reference.resolution.get("manualLocation"):
        try:
            point = coordinates(reference.resolution["manualLocation"]["location"])
        except (KeyError, TypeError, ValueError):
            return None
        return {**point, "reviewState": reference.resolution.get("reviewState", "unreviewed")}
    if project.latitude is not None and project.longitude is not None:
        return {
            "latitude": float(project.latitude), "longitude": float(project.longitude),
            "reviewState": "accepted",
        }
    return None


def resolve_project_location(value):
    try:
        return direct_location(
            latitude=value.get("latitude"), longitude=value.get("longitude"),
            maps_url=value.get("mapsUrl", ""),
        )
    except (ValueError, TypeError, OverflowError, OSError):
        raise ValidationError({"location": (
            "This location could not be resolved. Use a single HTTPS Google Maps pin/share link "
            "or latitude (-90 to 90) and longitude (-180 to 180)."
        )}) from None


def submit_project_location(project, reference, actor):
    current = project_location_data(project)
    if current and current["reviewState"] in {"unreviewed", "accepted"} and all(
        current[key] == reference["location"][key] for key in ("latitude", "longitude")
    ):
        return False
    from uuid import uuid4
    from .project_sources import commit_references
    commit_references(project, actor, {"projectVersion": project.updated_at.isoformat(),
        "manualLocation": reference}, str(uuid4()))
    return True
