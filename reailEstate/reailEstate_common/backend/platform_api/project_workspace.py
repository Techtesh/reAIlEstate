"""Explicit platform-editor access to the shared project workspace."""
from rest_framework.permissions import BasePermission


def platform_project_editor(user):
    return bool(user and user.is_authenticated and user.is_active and user.is_staff
                and user.has_perm("platform_api.change_project"))


class AdminProjectWorkspacePermission(BasePermission):
    def has_permission(self, request, view):
        return platform_project_editor(request.user)


class AdminLayoutWorkspacePermission(AdminProjectWorkspacePermission):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and (
            request.method in {"GET", "HEAD", "OPTIONS"}
            or request.user.has_perm("platform_api.change_floorplan")
        )


def project_evidence_ids(project):
    from .models import Project
    from .project_lifecycle import project_family_ids
    return list(Project.objects.filter(pk__in=project_family_ids(project),
        organization_id=project.organization_id).values_list('pk', flat=True))


def admin_media_tools(user, project, plan=None):
    """Expose existing admin workflows only with their model permissions."""
    from django.urls import reverse
    from urllib.parse import urlencode

    if not platform_project_editor(user):
        return []
    workspace = "/admin/project-workspace/?" + urlencode({"organization": str(project.organization_id), "project": str(project.pk)})
    if plan is not None:
        if not user.has_perm("platform_api.change_floorplan"):
            return []
        return [
            {"label": "Correct extracted room dimensions and details",
             "url": workspace + "&" + urlencode({"section": "floor-plans" if plan.plan_scope == "floor" else "layouts", "plan": str(plan.pk)})},
            {"label": "Review floor plan record",
             "url": "/admin/records/floor-plans/?" + urlencode({"project": str(project.pk), "record": str(plan.pk)})},
        ]
    tools = [{"label": "Project administration", "url": workspace},
             {"label": "Review source references and location", "url": workspace + "&section=sources"}]
    destinations = {"floorplan": "floor-plans", "projectamenity": "project-amenities", "floorplanasset": "assets",
        "factcandidate": "candidates", "reviewtask": "review-tasks", "evidencedocument": "documents", "projectfact": "facts"}
    for model, label in (("floorplan", "Review floor plans and layouts"),
                         ("projectamenity", "Correct and review project amenities"),
                         ("floorplanasset", "Review media assets and categories"),
                         ("factcandidate", "Review extracted facts and amenity evidence"),
                         ("reviewtask", "Review tasks"), ("evidencedocument", "Review source documents"),
                         ("projectfact", "Review project facts")):
        if user.has_perm(f"platform_api.change_{model}"):
            tools.append({"label": label, "url": "/admin/records/" + destinations[model] + "/?" + urlencode({"project": str(project.pk)})})
    return tools
