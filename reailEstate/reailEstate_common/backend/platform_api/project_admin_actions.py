"""Focused, permission-preserving project-admin actions."""


from django.contrib import messages
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse

from .project_lifecycle import retire_project


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
