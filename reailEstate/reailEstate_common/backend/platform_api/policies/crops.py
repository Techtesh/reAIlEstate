"""Crop limits, destination resolution and authorization shared by callers.

This policy has no cart or evaluation dependency. Only destination resolution
loads ORM models; importing limits or checking an actor does not load models.
"""

from django.core.exceptions import PermissionDenied


CART_LIMIT = 20
MAX_DRAFT_BYTES = 20 * 1024 * 1024


def crop_destination(project):
    """Resolve only an applied, same-organization merge; never reactivate sources."""
    from ..models import Project, ProjectMerge
    source = Project.objects.get(pk=project.pk)
    if source.status == "retired":
        raise ValueError("This source project is retired; crop additions are disabled.")
    merge = ProjectMerge.objects.filter(source_project=source, state="applied").select_related("target_project").first()
    if not merge:
        if source.status == "merged":
            raise ValueError("This merged source has no active destination. Repair its project merge before cropping.")
        return source
    target = merge.target_project
    if target.organization_id != source.organization_id or target.status in {"retired", "merged"} or ProjectMerge.objects.filter(
        source_project=target, state="applied"
    ).exists():
        raise ValueError("The merged project's destination is unavailable for crop additions.")
    return target


def authorize_crops(actor, project):
    """Allow platform editors or scoped organization builders on active projects."""
    from ..builder_access import builder_can_manage
    if not actor or not actor.is_active:
        raise PermissionDenied
    if not (actor.is_staff and actor.has_perm("platform_api.change_floorplan")) and not builder_can_manage(actor, project):
        raise PermissionDenied
    if project.status in {"retired", "merged"}:
        raise ValueError("Use an active project for crop additions.")