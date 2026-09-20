"""Builder location proposals use the existing independent location review workflow."""

from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .external_sources import coordinates, direct_location
from .models import ReviewTask


BUILDER_LOCATION_SOURCE = "builder_project_location"


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
    # An older builder proposal must not be approved after a replacement is submitted.
    for task in project.review_tasks.select_for_update().filter(
        task_type="external_sources", state="open", resolution__source=BUILDER_LOCATION_SOURCE,
    ):
        task.state = "resolved"
        task.completed_at = timezone.now()
        task.version += 1
        task.resolution = {**task.resolution, "reviewState": "superseded"}
        task.save(update_fields=["state", "completed_at", "version", "resolution", "updated_at"])
    ReviewTask.objects.create(project=project, task_type="external_sources", priority=3, resolution={
        "manualLocation": reference, "submittedBy": actor.pk,
        "reviewState": "unreviewed", "source": BUILDER_LOCATION_SOURCE,
    })
    return True
