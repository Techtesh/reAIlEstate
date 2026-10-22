"""Focused, permission-preserving project-admin actions."""

from uuid import uuid4

from django.contrib import messages
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse

from .amenity_matching import find_amenities
from .models import Amenity, AuditEvent, ProjectAmenity
from .project_lifecycle import retire_project
from .project_outputs import AMENITIES


def project_retirement_response(admin_site, request, project):
    """Render or execute the existing safe-retirement admin flow."""
    cancel_url = reverse("admin:platform_api_project_change", args=[project.pk], current_app=admin_site.name)
    if request.method == "POST":
        if request.POST.get("confirm") != "yes":
            return HttpResponseRedirect(cancel_url)
        try:
            retire_project(project.id, request.user, "ADMIN_DELETE_CONFIRMED")
        except ValueError as exc:
            messages.error(request, f"Project could not be retired: {exc}")
            return HttpResponseRedirect(cancel_url)
        messages.success(request, "Project retired. Evidence, layouts, recommendation history, and audit records were preserved.")
        return HttpResponseRedirect(reverse("admin:platform_api_project_changelist", current_app=admin_site.name))
    context = {
        **admin_site.each_context(request), "opts": project._meta,
        "original": project, "object": project,
        "title": "Delete project", "cancel_url": cancel_url,
    }
    return TemplateResponse(request, "admin/platform_api/project_delete_confirmation.html", context)


def add_manual_amenities(request, project, text, message_user):
    """Attach recognized, reviewer-owned manual amenity claims and audit the mutation."""
    text = text.strip()
    if not text:
        return
    categories = {code: category for category, codes in AMENITIES.items() for code in codes}
    matches = {match.code for match in find_amenities(text) if match.code in categories}
    if not matches:
        message_user(request, "No recognized amenity names were found; no manual claims were added.", level=messages.WARNING)
        return
    for code in sorted(matches):
        amenity, _ = Amenity.objects.get_or_create(taxonomy_code=code, defaults={
            "display_name": code.replace("_", " ").title(), "category": categories[code],
        })
        claim, _ = ProjectAmenity.objects.get_or_create(project=project, amenity=amenity)
        summary = dict(claim.evidence_summary)
        manual = list(summary.get("manual", []))
        entry = {"text": text[:500], "source": "admin_manual", "reviewState": "reviewed"}
        if entry not in manual:
            manual.append(entry)
        summary["manual"] = manual
        summary["reviewState"] = "reviewed"
        claim.evidence_summary = summary
        claim.save(update_fields=["evidence_summary", "updated_at"])
    AuditEvent.objects.create(
        actor=request.user, action="project.amenities_added_manually", entity_type="Project",
        entity_id=project.pk, correlation_id=uuid4(), metadata={"amenities": sorted(matches)},
    )
    message_user(request, f"Added {len(matches)} manual amenity claim(s).", level=messages.SUCCESS)