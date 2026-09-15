"""Owner-bound external lookup drafts, separate from verified project facts."""

import json
from uuid import UUID, uuid4

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from .external_sources import GoogleProjectMaps, REGISTRATION, REGISTRY_SEARCH, registry_lookup, station_routes
from .ingestion_runtime import WorkAlreadyClaimed, work_lock
from .models import AuditEvent, Project, ReviewTask
from .project_enrichment import source_url


TTL = 900


class MapsSearchForm(forms.Form):
    query = forms.CharField(max_length=500, min_length=3, label="Project name and address")


class RegistryEvidenceForm(forms.Form):
    registration_number = forms.RegexField(REGISTRATION, max_length=64, label="MahaRERA number")
    source_url = forms.URLField(max_length=2048, label="MahaRERA project record URL")
    checked_on = forms.DateField(label="Evidence date", widget=forms.DateInput(attrs={"type": "date"}))
    evidence = forms.CharField(max_length=3000, widget=forms.Textarea(attrs={"rows": 4}), label="Project details from the record")

    def clean_registration_number(self):
        number = self.cleaned_data["registration_number"].strip().upper()
        if not REGISTRATION.fullmatch(number):
            raise forms.ValidationError("Enter an exact project registration number.")
        return number

    def clean_source_url(self):
        value = self.cleaned_data["source_url"]
        if not source_url(value, maharera=True):
            raise forms.ValidationError("Use an HTTPS MahaRERA project record URL.")
        return value

    def clean_checked_on(self):
        value = self.cleaned_data["checked_on"]
        if value > timezone.localdate():
            raise forms.ValidationError("Evidence date cannot be in the future.")
        return value


def draft_key(project, actor, token):
    try:
        token = str(UUID(str(token)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Lookup draft expired. Start a new lookup.") from None
    return f"external-sources:{project.pk}:{actor.pk}:{token}"


def commit_references(project, actor, draft, token):
    if not actor.is_active or not actor.is_staff or not actor.has_perm("platform_api.change_project"):
        raise PermissionDenied
    with transaction.atomic():
        locked = Project.objects.select_for_update().get(pk=project.pk)
        if locked.status in {"retired", "merged"} or locked.updated_at.isoformat() != draft["projectVersion"]:
            raise ValueError("Project changed. Start a fresh lookup before committing.")
        existing = ReviewTask.objects.filter(project=locked, task_type="external_sources", resolution__draftId=token).first()
        if existing:
            return existing
        references = {"draftId": token, "submittedBy": actor.pk, "reviewState": "unreviewed"}
        if draft.get("selected"):
            references["googlePlaceId"] = draft["selected"]["placeId"]
        if draft.get("registry"):
            references["maharera"] = draft["registry"]
        if not (references.get("googlePlaceId") or references.get("maharera")):
            raise ValueError("Select a project match or add registry evidence first.")
        task = ReviewTask.objects.create(project=locked, task_type="external_sources", priority=3, resolution=references)
        AuditEvent.objects.create(actor=actor, action="project.external_sources_submitted", entity_type="ReviewTask",
                                  entity_id=task.pk, correlation_id=uuid4(), metadata={"projectId": str(locked.pk)})
        return task


class ExternalSourcesAdminMixin:
    def external_sources_view(self, request, object_id):
        project = self.get_object(request, str(object_id))
        if project is None:
            raise Http404
        if not request.user.is_active or not request.user.is_staff or not self.has_change_permission(request, project):
            raise PermissionDenied
        url = reverse("admin:platform_api_project_external_sources", args=[project.pk])
        token = request.POST.get("draft_token") if request.method == "POST" else request.GET.get("draft")
        search_form = MapsSearchForm(initial={"query": f"{project.submitted_name}, {project.locality}, Maharashtra, India"})
        registry_form = RegistryEvidenceForm(initial={"registration_number": project.maha_rera_id, "checked_on": timezone.localdate()})
        draft, error = None, None
        if token:
            try:
                draft = cache.get(draft_key(project, request.user, token))
            except ValueError:
                pass
        if request.method == "POST":
            action = request.POST.get("action")
            try:
                if project.status in {"retired", "merged"}:
                    raise ValueError("Use an active canonical project.")
                if action == "search":
                    search_form = MapsSearchForm(request.POST)
                    if not search_form.is_valid():
                        raise ValueError("Enter a project name and locality.")
                    places = GoogleProjectMaps().search(search_form.cleaned_data["query"])
                    token = str(uuid4())
                    draft = {"projectVersion": project.updated_at.isoformat(), "places": places,
                             "registry": draft.get("registry") if draft else None}
                    if not places:
                        messages.warning(request, "No project matches returned. Refine the name and address.")
                elif action in {"registry_lookup", "registry_manual"}:
                    if not draft:
                        token = str(uuid4())
                        draft = {"projectVersion": project.updated_at.isoformat(), "places": []}
                    if action == "registry_lookup":
                        number = request.POST.get("registration_number", "").strip().upper()
                        result = registry_lookup(number)
                        if result["status"] != "available":
                            raise ValueError("MahaRERA adapter returned no matching record. Open the official search and add dated evidence below.")
                        draft["registry"] = result | {"registrationNumber": number, "evidenceMethod": "approved_adapter"}
                    else:
                        registry_form = RegistryEvidenceForm(request.POST)
                        if not registry_form.is_valid():
                            raise ValueError("Check the registry evidence fields.")
                        values = registry_form.cleaned_data
                        draft["registry"] = {"registrationNumber": values["registration_number"], "sourceUrl": values["source_url"],
                                             "checkedOn": values["checked_on"].isoformat(), "evidence": values["evidence"],
                                             "evidenceMethod": "manual_website", "status": "unreviewed"}
                elif action == "refresh_saved":
                    reference_id = forms.UUIDField().clean(request.POST.get("reference_id"))
                    task = ReviewTask.objects.filter(project=project, task_type="external_sources", pk=reference_id).first()
                    if not task or not task.resolution.get("googlePlaceId"):
                        raise ValueError("Saved Google reference is unavailable.")
                    place = GoogleProjectMaps().details(task.resolution["googlePlaceId"])
                    token = str(uuid4())
                    draft = {"projectVersion": project.updated_at.isoformat(), "places": [place], "selected": place,
                             "routes": station_routes(place, 10000)}
                else:
                    if not draft:
                        raise ValueError("Lookup draft expired. Start a new lookup.")
                    if action == "select":
                        place = next((item for item in draft["places"] if item["placeId"] == request.POST.get("place_id")), None)
                        if not place:
                            raise ValueError("Select a match from this lookup.")
                        radius = int(request.POST.get("radius", "10000"))
                        draft["routes"] = station_routes(place, radius)
                        draft["selected"] = place
                    elif action == "discard":
                        cache.delete(draft_key(project, request.user, token))
                        messages.success(request, "External lookup draft discarded.")
                        return HttpResponseRedirect(url)
                    elif action == "commit":
                        if request.POST.get("confirm") != "on":
                            raise ValueError("Confirm submitting these source references for review.")
                        with work_lock("external-sources", project.pk):
                            commit_references(project, request.user, draft, token)
                        cache.delete(draft_key(project, request.user, token))
                        messages.success(request, "Source references submitted for review. Accepted project facts are unchanged.")
                        return HttpResponseRedirect(url)
                    else:
                        raise ValueError("Choose a valid external-source action.")
                cache.set(draft_key(project, request.user, token), draft, TTL)
                return HttpResponseRedirect(url + "?draft=" + token)
            except (ValueError, ValidationError, WorkAlreadyClaimed) as exc:
                error = str(exc) if isinstance(exc, ValueError) else "Invalid reference." if isinstance(exc, ValidationError) else "Project is busy. Retry shortly."
        response = TemplateResponse(request, "admin/platform_api/external_sources.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta, "original": project,
            "title": f"External sources: {project.submitted_name}", "search_form": search_form,
            "registry_form": registry_form, "draft": draft, "draft_token": token or "", "source_error": error,
            "registry_search_url": REGISTRY_SEARCH, "registry_number": project.maha_rera_id or "",
            "registry_json": json.dumps(draft.get("registry"), indent=2) if draft and draft.get("registry") else "",
            "maps_configured": bool(getattr(settings, "GOOGLE_MAPS_API_KEY", "")),
            "registry_configured": bool(getattr(settings, "PROJECT_ENRICHMENT_BACKEND", "")),
            "references": ReviewTask.objects.filter(project=project, task_type="external_sources").order_by("-created_at")[:20],
        })
        response["Cache-Control"] = "private, no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response