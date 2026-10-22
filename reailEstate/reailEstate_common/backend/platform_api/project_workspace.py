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
