"""Independent source review and station operations for shared project submissions."""

import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID, uuid4

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.db.models.fields.json import KeyTextTransform
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from .external_sources import GoogleProjectMaps, REGISTRY_SEARCH, coordinates, direct_location, station_routes
from .ingestion_runtime import WorkAlreadyClaimed, work_lock
from .models import AuditEvent, Project, ReviewTask
from .project_workspace_admin import workspace_url


TTL = 900


GOOGLE_MATCH_LABELS = {
    "matched": "Matched", "ambiguous": "Ambiguous", "no_match": "No strict match",
    "failed": "Lookup failed", "insufficient_project_data": "Incomplete project details",
    "skipped_existing": "Reference already saved", "not_run": "No lookup result",
}
LOCATION_REFERENCE_QUERY = models.Q(resolution__googlePlaceId__isnull=False) | models.Q(resolution__manualLocation__isnull=False)


def google_match_annotations(queryset):
    report_path = Path(settings.WORKSPACE_DATA_ROOT) / "output" / "reports" / "google-project-places.json"
    groups = {}
    try:
        if report_path.stat().st_size > 20_000_000:
            raise ValueError
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(report, dict) or not isinstance(report.get("projects"), list):
            raise ValueError
        for row in report["projects"]:
            if not isinstance(row, dict) or row.get("status") not in GOOGLE_MATCH_LABELS:
                raise ValueError
            project_id = UUID(row["projectId"])
            groups.setdefault(row["status"], []).append(project_id)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        groups = {}
    references = ReviewTask.objects.filter(LOCATION_REFERENCE_QUERY, project_id=models.OuterRef("pk"),
        task_type="external_sources").order_by("-created_at", "-pk")
    return queryset.annotate(
        google_match_status=models.Case(*[
            models.When(pk__in=ids, then=models.Value(status)) for status, ids in groups.items()
        ], default=models.Value("not_run"), output_field=models.CharField()),
        google_place_id=models.Subquery(references.annotate(
            place=KeyTextTransform("googlePlaceId", "resolution")).values("place")[:1]),
        google_review_state=models.Subquery(references.annotate(
            review=KeyTextTransform("reviewState", "resolution")).values("review")[:1]),
        manual_location=models.Subquery(references.values("resolution__manualLocation")[:1]),
    )


class GoogleMatchFilter(admin.SimpleListFilter):
    title = "Google Maps"
    parameter_name = "google_match"

    def lookups(self, request, model_admin):
        return [("incomplete", "Incomplete data"), ("needs_lookup", "Needs lookup"), ("missing_reference", "Missing reference"),
                ("awaiting_review", "Awaiting review"),
                ("ambiguous", "Ambiguous"), ("no_match", "No strict match"), ("matched", "Matched"),
                ("failed", "Lookup failed"), ("not_run", "No lookup result")]

    def queryset(self, request, queryset):
        if self.value() == "incomplete":
            complete = models.Q(google_review_state="accepted", latitude__isnull=False, longitude__isnull=False)
            return queryset.exclude(complete).exclude(status__in=["retired", "merged"])
        if self.value() == "needs_lookup":
            return queryset.filter(google_place_id__isnull=True, manual_location__isnull=True).exclude(status__in=["retired", "merged"])
        if self.value() == "missing_reference":
            return queryset.filter(google_place_id__isnull=True, manual_location__isnull=True)
        if self.value() == "awaiting_review":
            return queryset.exclude(google_place_id__isnull=True, manual_location__isnull=True).filter(
                models.Q(google_review_state="unreviewed") | models.Q(google_review_state__isnull=True)
            ).exclude(status__in=["retired", "merged"])
        if self.value() in {"ambiguous", "no_match", "failed", "not_run", "insufficient_project_data"}:
            return queryset.filter(google_match_status=self.value(), google_place_id__isnull=True,
                                   manual_location__isnull=True).exclude(status__in=["retired", "merged"])
        if self.value() in GOOGLE_MATCH_LABELS:
            return queryset.filter(google_match_status=self.value())
        return queryset


class MahaRERAFilter(admin.SimpleListFilter):
    title = "MahaRERA ID"
    parameter_name = "maharera"

    def lookups(self, request, model_admin):
        return [("missing", "Missing ID"), ("present", "ID present")]

    def queryset(self, request, queryset):
        missing = models.Q(maha_rera_id__isnull=True) | models.Q(maha_rera_id="")
        if self.value() == "missing":
            return queryset.filter(missing)
        if self.value() == "present":
            return queryset.exclude(missing)
        return queryset


def saved_station_routes(project):
    task = ReviewTask.objects.filter(LOCATION_REFERENCE_QUERY, project=project,
        task_type="external_sources").order_by("-created_at", "-pk").first()
    if not task:
        return None
    routes = task.resolution.get("stationRoutes")
    if isinstance(routes, dict) and isinstance(routes.get("groups"), list):
        return routes

    # Accepting a replacement manual pin creates a new reference.  If it is the
    # same pin as an older reference, retain that reference's already-reviewed
    # route results rather than making them disappear from the admin view.
    if project.latitude is not None and project.longitude is not None:
        current = {"latitude": float(project.latitude), "longitude": float(project.longitude)}
        for previous in ReviewTask.objects.filter(LOCATION_REFERENCE_QUERY, project=project,
                task_type="external_sources").exclude(pk=task.pk).order_by("-created_at", "-pk"):
            previous_routes = previous.resolution.get("stationRoutes")
            if not isinstance(previous_routes, dict) or not isinstance(previous_routes.get("groups"), list):
                continue
            manual = previous.resolution.get("manualLocation")
            origin = previous_routes.get("origin")
            candidate = origin or (manual.get("location") if isinstance(manual, dict) else None)
            try:
                location = coordinates(candidate)
            except (TypeError, ValueError):
                continue
            if (abs(location["latitude"] - current["latitude"]) <= 0.000001 and
                    abs(location["longitude"] - current["longitude"]) <= 0.000001):
                return previous_routes
    path = Path(settings.WORKSPACE_DATA_ROOT) / "output" / "reports" / "google-project-routes.json"
    if not path.is_file():
        path = Path(settings.BASE_DIR) / "reports" / "google-project-routes.json"
    try:
        if not path.is_file() or path.stat().st_size > 20_000_000:
            return None
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(report, dict) or not isinstance(report.get("projects"), list):
        return None
    row = next((item for item in report["projects"] if isinstance(item, dict)
        and item.get("projectId") == str(project.pk) and item.get("status") == "enriched"
        and item.get("reviewTaskId") == str(task.pk)), None)
    routes = row.get("routes") if row else None
    return routes if isinstance(routes, dict) and isinstance(routes.get("groups"), list) else None


def station_choice_key(station):
    import hashlib

    return hashlib.sha256(json.dumps({key: station.get(key) for key in (
        "kind", "name", "line", "location", "placeId", "candidateId")}, sort_keys=True).encode()).hexdigest()


def route_revision(routes):
    import hashlib

    return hashlib.sha256(json.dumps(routes, sort_keys=True).encode()).hexdigest()


class TransitStationForm(forms.Form):
    project_version = forms.DateTimeField(widget=forms.HiddenInput)
    route_revision = forms.CharField(widget=forms.HiddenInput)
    railway = forms.ChoiceField(label="Railway station", required=False)
    metro = forms.ChoiceField(label="Metro station", required=False)

    def __init__(self, *args, project, routes, **kwargs):
        super().__init__(*args, **kwargs)
        selected = project.transit_station_choices or {}
        self.initial.update(project_version=project.updated_at, route_revision=route_revision(routes))
        for kind in ("railway", "metro"):
            options = {}
            for group in (routes or {}).get("groups", []):
                for station in group.get("stations", []):
                    if station.get("kind") != kind:
                        continue
                    try:
                        coordinates(station.get("location"))
                    except ValueError:
                        continue
                    walking = station.get("walking", {})
                    distance = walking.get("distanceMeters")
                    label = f"{station.get('name', 'Station')} - {station.get('line') or group.get('label', kind)}"
                    if walking.get("status") == "available" and type(distance) is int and distance >= 0:
                        label += f" - {distance} m walking"
                    else:
                        label += " - walking distance unavailable"
                    options[station_choice_key(station)] = label
            self.fields[kind].choices = [("", "Automatic (shortest available walking route)"), *options.items()]
            self.initial[kind] = selected.get(kind) if selected.get(kind) in options else ""


def save_transit_station_choices(project, actor, data):
    if not actor.is_active or not actor.is_staff or not actor.has_perm("platform_api.change_project"):
        raise PermissionDenied
    with transaction.atomic():
        current = Project.objects.select_for_update().get(pk=project.pk)
        if current.status in {"retired", "merged"}:
            raise ValueError("Use an active canonical project.")
        routes = saved_station_routes(current)
        form = TransitStationForm(data, project=current, routes=routes)
        if not form.is_valid():
            raise ValueError("Choose stations from the current saved routes. Reload and try again.")
        if (form.cleaned_data["project_version"] != current.updated_at
                or form.cleaned_data["route_revision"] != route_revision(routes)):
            raise ValueError("Project or routes changed. Reload before saving station choices.")
        current.transit_station_choices = {kind: form.cleaned_data[kind] for kind in ("railway", "metro")
                                           if form.cleaned_data[kind]}
        current.save(update_fields=["transit_station_choices", "updated_at"])
        AuditEvent.objects.create(actor=actor, action="project.transit_stations_selected", entity_type="Project",
            entity_id=current.pk, correlation_id=uuid4(), metadata=current.transit_station_choices)


def project_transit_maps(project):
    from .external_sources import route_link

    views = [{"kind": kind, "label": label, "station": None} for kind, label in (
        ("metro", "Metro"), ("railway", "Railway"))]
    routes = saved_station_routes(project) or {}
    location_label = "Saved project location"
    try:
        origin = coordinates({"latitude": float(project.latitude), "longitude": float(project.longitude)})
    except (TypeError, ValueError, OverflowError):
        reference = ReviewTask.objects.filter(LOCATION_REFERENCE_QUERY, project=project,
            task_type="external_sources").order_by("-created_at", "-pk").first()
        if not reference or reference.resolution.get("reviewState") == "rejected":
            return {"views": views, "has_location": False}
        manual = manual_reference_details(reference.resolution.get("manualLocation"))
        if manual:
            origin = manual["location"]
            location_label = "Saved manual pin (not applied to project location)"
        elif (reference.resolution.get("googlePlaceId")
                and routes.get("originPlaceId") == reference.resolution["googlePlaceId"]):
            try:
                origin = coordinates(routes.get("origin"))
            except ValueError:
                return {"views": views, "has_location": False}
            location_label = "Saved Google reference pin (not applied to project location)"
        else:
            return {"views": views, "has_location": False}
    pin = f"{origin['latitude']},{origin['longitude']}"
    location_map = {
        "location_label": location_label,
        "pin_embed_url": "https://maps.google.com/maps?" + urlencode({"q": pin, "z": 16, "output": "embed"}),
        "pin_url": "https://www.google.com/maps/search/?" + urlencode({"api": 1, "query": pin}),
    }
    route_origin = origin
    if "origin" in routes:
        try:
            route_origin = coordinates(routes["origin"])
        except ValueError:
            routes = {}
    candidates = {"metro": [], "railway": []}
    route_errors = {"metro": [], "railway": []}
    for group in routes.get("groups", []):
        if not isinstance(group, dict) or not isinstance(group.get("stations"), list):
            continue
        for station in group["stations"]:
            if not isinstance(station, dict) or station.get("kind") not in candidates:
                continue
            walking = station.get("walking")
            if isinstance(walking, dict) and isinstance(walking.get("error"), str):
                route_errors[station["kind"]].append(walking["error"])
            if not isinstance(walking, dict):
                continue
            distance = walking.get("distanceMeters")
            if walking.get("status") == "available" and (type(distance) is not int or distance < 0):
                continue
            if walking.get("status") != "available":
                distance = None
            try:
                destination = coordinates(station.get("location"))
            except ValueError:
                continue
            candidates[station["kind"]].append((distance, station, destination))
    statuses = {"planned": "Upcoming: planned", "under_construction": "Upcoming: under construction",
                "operational": "Existing"}
    for view in views:
        errors = [error for error in routes.get("errors", []) if isinstance(error, str)
              and error != "Some station catalogue candidates are unavailable or invalid. Review the candidate CSV."] + route_errors[view["kind"]]
        view["errors"] = list(dict.fromkeys(errors))[:5]
        view["access_denied"] = any("HTTP 403" in error for error in view["errors"])
        if not candidates[view["kind"]]:
            continue
        selected = (getattr(project, "transit_station_choices", None) or {}).get(view["kind"])
        chosen = next((item for item in candidates[view["kind"]] if station_choice_key(item[1]) == selected), None)
        available = [item for item in candidates[view["kind"]] if item[0] is not None]
        if chosen is None and not available:
            continue
        distance, station, destination = chosen or min(available, key=lambda item: item[0])
        link_origin, link_destination = route_origin, destination
        if station["walking"].get("endpointMode") == "places":
            link_origin = route_origin | {"placeId": routes.get("originPlaceId")}
            link_destination = destination | {"placeId": station.get("placeId")}
        view.update({
            "station": str(station.get("name", "Station")), "distance_meters": distance,
            "selected": chosen is not None,
            "service_status": statuses.get(station.get("serviceStatus"), "Service status unverified"),
            "directions_url": route_link(link_origin, link_destination, "walking"),
            "different_origin": route_origin != origin,
            "embed_url": "https://maps.google.com/maps?" + urlencode({
                "saddr": f"{route_origin['latitude']},{route_origin['longitude']}",
                "daddr": f"{destination['latitude']},{destination['longitude']}",
                "dirflg": "w", "output": "embed",
            }),
        })
    return {"views": views, "has_location": True, "retrieved_at": routes.get("retrievedAt", ""), **location_map}


def location_reviewer_allowed(task, actor):
    return (not (getattr(settings, "PROJECT_LOCATION_REQUIRE_SECOND_APPROVER", True)
                 or task.resolution.get("source") in {"builder_project_location", "project_workspace"})
            or str(task.resolution.get("submittedBy")) != str(actor.pk))


def _google_reference(project, actor, task_id, *, accepting=False, allow_manual=False):
    if not actor or not actor.is_active or not actor.is_staff or not actor.has_perm("platform_api.change_project"):
        raise PermissionDenied
    current = Project.objects.get(pk=project.pk)
    if (current.status in {"retired", "merged"} or "mergedIntoProjectId" in current.address
            or Project.objects.filter(pk=current.pk, merge_out__state="applied").exists()):
        raise ValueError("Use an active canonical project.")
    task = ReviewTask.objects.filter(pk=task_id, project=current, task_type="external_sources").first()
    if not task or not isinstance(task.resolution, dict) or not (task.resolution.get("googlePlaceId")
            or (allow_manual and manual_reference_details(task.resolution.get("manualLocation")))):
        raise ValueError("Saved Google reference is unavailable.")
    if accepting and task.state != "open":
        raise ValueError("Reference must be open before approval.")
    if accepting and not location_reviewer_allowed(task, actor):
        raise ValueError("An open reference must be approved by a different reviewer.")
    return current, task


def _check_google_version(current, task, previous_project, previous_task):
    if (current.status in {"retired", "merged"} or "mergedIntoProjectId" in current.address
            or Project.objects.filter(pk=current.pk, merge_out__state="applied").exists()
            or current.updated_at != previous_project.updated_at or task.version != previous_task.version
            or task.updated_at != previous_task.updated_at or task.resolution != previous_task.resolution
            or task.state != previous_task.state):
        raise ValueError("Project or reference changed. Reload before saving.")


def apply_google_reference(project, actor, task_id):
    previous_project, previous_task = _google_reference(project, actor, task_id, accepting=True)
    place = GoogleProjectMaps().details(previous_task.resolution["googlePlaceId"])
    if place["placeId"] != previous_task.resolution["googlePlaceId"]:
        raise ValueError("Google returned a different place reference. Start a fresh lookup.")
    _save_google_location(previous_project, previous_task, place, actor)
    return place


def _save_google_location(previous_project, previous_task, place, actor, routes=None, *, audit_action=None):
    location = coordinates(place["location"])
    with transaction.atomic():
        current = Project.objects.select_for_update().get(pk=previous_project.pk)
        task = ReviewTask.objects.select_for_update().get(pk=previous_task.pk)
        _check_google_version(current, task, previous_project, previous_task)
        if not location_reviewer_allowed(task, actor):
            raise ValueError("This location requires a different reviewer.")
        approval_policy = {
            "secondApproverRequired": (getattr(settings, "PROJECT_LOCATION_REQUIRE_SECOND_APPROVER", True)
                                      or task.resolution.get("source") in {"builder_project_location", "project_workspace"}),
            "selfApproved": str(task.resolution.get("submittedBy")) == str(actor.pk),
        }
        current.latitude = Decimal(str(location["latitude"]))
        current.longitude = Decimal(str(location["longitude"]))
        current.save(update_fields=["latitude", "longitude", "updated_at"])
        task.state = "resolved"
        task.completed_at = timezone.now()
        task.version += 1
        task.resolution = task.resolution | {"reviewState": "accepted", "acceptedBy": actor.pk,
            "acceptedAt": task.completed_at.isoformat(), "approvalPolicy": approval_policy}
        if routes is not None:
            task.resolution["stationRoutes"] = routes
        task.save(update_fields=["state", "completed_at", "version", "resolution", "updated_at"])
        action = audit_action or ("project.google_location_accepted" if routes is None else "project.google_location_routes_enriched")
        AuditEvent.objects.create(actor=actor, action=action, entity_type="ReviewTask",
            entity_id=task.pk, correlation_id=uuid4(), metadata={"projectId": str(current.pk), **approval_policy})


def manual_reference_details(reference):
    try:
        location = coordinates(reference["location"])
        return direct_location(location["latitude"], location["longitude"])
    except (KeyError, TypeError, ValueError):
        return None


def apply_manual_reference(project, actor, task_id, project_version, reference_version):
    if not actor.is_active or not actor.is_staff or not actor.has_perm("platform_api.change_project"):
        raise PermissionDenied
    current = Project.objects.get(pk=project.pk)
    task = ReviewTask.objects.filter(pk=task_id, project=current, task_type="external_sources").first()
    if not task or not manual_reference_details(task.resolution.get("manualLocation")):
        raise ValueError("Saved manual location is unavailable.")
    if task.state != "open":
        raise ValueError("Reference must be open before approval.")
    if not location_reviewer_allowed(task, actor):
        raise ValueError("An open reference must be approved by a different reviewer.")
    if current.updated_at != project_version or task.version != reference_version:
        raise ValueError("Project or reference changed. Reload before saving.")
    _save_google_location(current, task, task.resolution["manualLocation"], actor,
                          audit_action="project.manual_location_accepted")


def enrich_google_reference(project, actor, task_id, radius):
    if radius not in {5000, 10000, 20000}:
        raise ValueError("Invalid station search radius.")
    previous_project, previous_task = _google_reference(project, actor, task_id)
    if not location_reviewer_allowed(previous_task, actor):
        raise ValueError("This location requires a different reviewer.")
    if previous_task.state != "open" and not (previous_task.state == "resolved"
            and previous_task.resolution.get("reviewState") == "accepted"):
        raise ValueError("Reference must be open or previously accepted.")
    maps = GoogleProjectMaps()
    place = maps.details(previous_task.resolution["googlePlaceId"])
    if place["placeId"] != previous_task.resolution["googlePlaceId"]:
        raise ValueError("Google returned a different place reference. Start a fresh lookup.")
    routes = station_routes(place, radius, maps)
    _save_google_location(previous_project, previous_task, place, actor, routes)
    return place, routes


def refresh_google_routes(project, actor, task_id, radius):
    if radius not in {5000, 10000, 20000}:
        raise ValueError("Invalid station search radius.")
    previous_project, previous_task = _google_reference(project, actor, task_id, allow_manual=True)
    maps = GoogleProjectMaps()
    place = manual_reference_details(previous_task.resolution.get("manualLocation"))
    if place is None:
        place = maps.details(previous_task.resolution["googlePlaceId"])
        if place["placeId"] != previous_task.resolution["googlePlaceId"]:
            raise ValueError("Google returned a different place reference. Start a fresh lookup.")
    routes = station_routes(place, radius, maps)
    routes["origin"] = coordinates(place["location"])
    if place.get("placeId"):
        routes["originPlaceId"] = place["placeId"]
    with transaction.atomic():
        current = Project.objects.select_for_update().get(pk=project.pk)
        task = ReviewTask.objects.select_for_update().get(pk=previous_task.pk)
        _check_google_version(current, task, previous_project, previous_task)
        task.version += 1
        task.resolution = task.resolution | {"stationRoutes": routes}
        task.save(update_fields=["resolution", "version", "updated_at"])
        AuditEvent.objects.create(actor=actor, action="project.google_station_routes_refreshed", entity_type="ReviewTask",
            entity_id=task.pk, correlation_id=uuid4(), metadata={"projectId": str(project.pk), "radius": radius})
    return routes


class ExternalSourcesAdminMixin:
    def external_sources_view(self, request, object_id):
        project = self.get_object(request, str(object_id))
        if project is None:
            raise Http404
        if not request.user.is_active or not request.user.is_staff or not self.has_change_permission(request, project):
            raise PermissionDenied
        url = reverse("admin:platform_api_project_external_sources", args=[project.pk])
        preserved_filters = request.GET.get("_changelist_filters", "")
        if preserved_filters:
            url += "?" + urlencode({"_changelist_filters": preserved_filters})
        error = None
        if request.method == "POST":
            action = request.POST.get("action")
            try:
                if project.status in {"retired", "merged"}:
                    raise ValueError("Use an active canonical project.")
                if action == "apply_manual":
                    if request.POST.get("confirm") != "on":
                        raise ValueError("Confirm applying this reviewed location to the project.")
                    apply_manual_reference(project, request.user,
                        forms.UUIDField().clean(request.POST.get("reference_id")),
                        forms.DateTimeField().clean(request.POST.get("project_version")),
                        forms.IntegerField(min_value=1).clean(request.POST.get("reference_version")))
                    messages.success(request, "Reviewed manual location applied to this project.")
                    return HttpResponseRedirect(url)
                elif action == "apply_saved":
                    if request.POST.get("confirm") != "on":
                        raise ValueError("Confirm applying this Google location to the project.")
                    reference_id = forms.UUIDField().clean(request.POST.get("reference_id"))
                    apply_google_reference(project, request.user, reference_id)
                    messages.success(request, "Reviewed Google location applied to this project.")
                    return HttpResponseRedirect(url)
                elif action == "save_station_choices":
                    save_transit_station_choices(project, request.user, request.POST)
                    messages.success(request, "Project station choices saved.")
                    return HttpResponseRedirect(url)
                elif action == "retry_saved_routes":
                    if not getattr(settings, "GOOGLE_MAPS_API_KEY", ""):
                        raise ValueError("Google Maps API is not configured on this server.")
                    reference_id = forms.UUIDField().clean(request.POST.get("reference_id"))
                    with work_lock("external-sources", project.pk):
                        routes = refresh_google_routes(project, request.user, reference_id, int(request.POST.get("radius", "10000")))
                    available = sum(station.get(mode, {}).get("status") == "available"
                        for group in routes["groups"] for station in group["stations"] for mode in ("walking", "driving"))
                    if available:
                        messages.success(request, f"Saved {available} available walking/driving route results.")
                    else:
                        messages.warning(request, "Station links were saved, but no route durations were returned. See provider diagnostics below.")
                    if request.POST.get("return_to") == "project":
                        return HttpResponseRedirect(reverse("admin:platform_api_project_change", args=[project.pk]) + "#project-transit-heading")
                    return HttpResponseRedirect(url)
                else:
                    raise ValueError("Use the shared project workspace to submit source references.")
            except (ValueError, ValidationError, WorkAlreadyClaimed) as exc:
                error = str(exc) if isinstance(exc, ValueError) else "Invalid reference." if isinstance(exc, ValidationError) else "Project is busy. Retry shortly."
        references = list(ReviewTask.objects.filter(project=project, task_type="external_sources").order_by("-created_at")[:20])
        for reference in references:
            reference.manual_details = manual_reference_details(reference.resolution.get("manualLocation"))
            reference.can_accept_manual = bool(reference.manual_details and reference.state == "open"
                and location_reviewer_allowed(reference, request.user))
            reference.can_accept_google = bool(reference.resolution.get("googlePlaceId") and reference.state == "open"
                and location_reviewer_allowed(reference, request.user))
        saved_routes = saved_station_routes(project)
        response = TemplateResponse(request, "admin/platform_api/external_sources.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta, "original": project,
            "title": f"Review sources: {project.submitted_name}", "source_error": error,
            "workspace_url": workspace_url(project, section="sources"),
            "registry_search_url": REGISTRY_SEARCH, "registry_number": project.maha_rera_id or "",
            "maps_configured": bool(getattr(settings, "GOOGLE_MAPS_API_KEY", "")),
            "location_second_approver_required": getattr(settings, "PROJECT_LOCATION_REQUIRE_SECOND_APPROVER", True),
            "registry_configured": bool(getattr(settings, "PROJECT_ENRICHMENT_BACKEND", "")),
            "references": references,
            "image_navigation": self.record_navigation(request, project, "admin:platform_api_project_external_sources"),
            "preserved_filters": self.get_preserved_filters(request),
            "saved_routes": saved_routes,
            "station_choice_form": TransitStationForm(project=project, routes=saved_routes) if saved_routes else None,
            "has_google_reference": ReviewTask.objects.filter(LOCATION_REFERENCE_QUERY, project=project,
                task_type="external_sources").exists(),
        })
        response["Cache-Control"] = "private, no-store"
        response["Referrer-Policy"] = "same-origin"
        return response
