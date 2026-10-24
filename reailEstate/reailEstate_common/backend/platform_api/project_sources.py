"""Source submissions shared by builder and platform project workspaces."""
from uuid import uuid4
from django import forms
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone
from .builder_access import builder_can_manage
from .external_sources import REGISTRATION, coordinates, direct_location
from .models import AuditEvent, Project, ReviewTask
from .project_enrichment import source_url
from .project_workspace import platform_project_editor

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


def commit_references(project, actor, draft, token):
    if project is None or not (platform_project_editor(actor) or builder_can_manage(actor, project)):
        raise PermissionDenied
    with transaction.atomic():
        locked = Project.objects.select_for_update().get(pk=project.pk)
        if locked.status in {"retired", "merged"} or locked.updated_at.isoformat() != draft["projectVersion"]:
            raise ValueError("Project changed. Start a fresh lookup before committing.")
        existing = ReviewTask.objects.filter(project=locked, task_type="external_sources", resolution__draftId=token).first()
        if existing:
            return existing
        references = {"draftId": token, "submittedBy": actor.pk, "reviewState": "unreviewed", "source": "project_workspace"}
        if draft.get("selected"):
            references["googlePlaceId"] = draft["selected"]["placeId"]
        if draft.get("manualLocation"):
            point = coordinates(draft["manualLocation"].get("location"))
            manual = direct_location(point["latitude"], point["longitude"])
            if not manual:
                raise ValueError("Direct location is invalid. Start a fresh draft.")
            references["manualLocation"] = manual | {
                "evidenceMethod": draft["manualLocation"].get("evidenceMethod", "manual_coordinates"),
            }
            if draft["manualLocation"].get("sourceUrlDigest"):
                references["manualLocation"]["sourceUrlDigest"] = draft["manualLocation"]["sourceUrlDigest"]
        if draft.get("registry"):
            references["maharera"] = draft["registry"]
        if draft.get("routes") and (draft.get("selected") or draft.get("manualLocation")):
            references["stationRoutes"] = draft["routes"] | {
                "origin": coordinates((draft.get("manualLocation") or draft["selected"])["location"])}
        if not (references.get("googlePlaceId") or references.get("manualLocation") or references.get("maharera")):
            raise ValueError("Select a project match or add registry evidence first.")
        if references.get("manualLocation") or references.get("googlePlaceId"):
            for previous in locked.review_tasks.select_for_update().filter(task_type="external_sources", state="open"):
                if previous.resolution.get("manualLocation") or previous.resolution.get("googlePlaceId"):
                    previous.state = "resolved"
                    previous.completed_at = timezone.now()
                    previous.version += 1
                    previous.resolution = {**previous.resolution, "reviewState": "superseded"}
                    previous.save(update_fields=["state", "completed_at", "version", "resolution", "updated_at"])
        task = ReviewTask.objects.create(project=locked, task_type="external_sources", priority=3, resolution=references)
        AuditEvent.objects.create(actor=actor, action="project.external_sources_submitted", entity_type="ReviewTask",
                                  entity_id=task.pk, correlation_id=uuid4(), metadata={"projectId": str(locked.pk)})
        return task
