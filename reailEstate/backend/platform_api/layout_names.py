"""Reviewer-owned layout aliases, separate from immutable extraction identity.

Dashboard integration: on its CSRF-protected ``save_layout_name`` POST action,
call ``save_layout_name(project, floor_plan_id, display_name, request.user)``.
The returned FloorPlan exposes display_name and display_title. Empty input clears
the alias. No extraction, output-file regeneration, or provenance edits occur.

Pure formatting is re-exported from domain.layout_titles for compatibility.
This persistence service depends on that leaf; models must import the leaf directly.
"""

from uuid import UUID, uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404

from .domain.layout_titles import (
    DISPLAY_NAME_MAX_LENGTH as DISPLAY_NAME_MAX_LENGTH,
    _BHK as _BHK,
    _GENERATED_SUFFIX as _GENERATED_SUFFIX,
    _UNSAFE_CATEGORIES as _UNSAFE_CATEGORIES,
    _UUID as _UUID,
    _safe_text as _safe_text,
    clean_layout_name as clean_layout_name,
    layout_display_title as layout_display_title,
    validate_layout_display_name as validate_layout_display_name,
)
from .models import AuditEvent, FloorPlan
from .feature_flags import require_enabled
from .policies.crops import crop_destination
from .project_lifecycle import project_family_ids


@transaction.atomic
def save_layout_name(project, floor_plan_id, display_name, actor, *, correlation_id=None):
    """Save an alias within the dashboard's project family, atomically audited.

    Requires active authenticated staff with change_floorplan. Raises
    PermissionDenied, ValidationError, or Http404 (missing/out-of-family plan).
    The caller must enforce POST and CSRF; this is a service, not a view.
    """
    if not (
        actor is not None and actor.is_authenticated and actor.is_active
        and actor.is_staff and actor.has_perm("platform_api.change_floorplan")
    ):
        raise PermissionDenied("Changing layout names requires floor-plan change permission.")
    require_enabled("media.layout_names", actor=actor, project=crop_destination(project))
    alias = validate_layout_display_name(display_name)
    try:
        plan_id = UUID(str(floor_plan_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError({"floor_plan_id": "Select a valid layout."}) from exc
    family_ids = project_family_ids(project)
    plan = get_object_or_404(
        FloorPlan.objects.select_for_update(), pk=plan_id,
        project_id__in=family_ids, document__project_id__in=family_ids,
    )
    previous = plan.display_name
    if previous == alias:
        return plan
    plan.display_name = alias
    plan.save(update_fields=["display_name", "updated_at"])
    AuditEvent.objects.create(
        actor=actor, action="layout.display_name_changed", entity_type="FloorPlan",
        entity_id=plan.pk, correlation_id=correlation_id or uuid4(),
        metadata={"projectId": str(plan.project_id), "dashboardProjectId": str(project.pk),
                  "previousDisplayName": previous, "displayName": alias},
    )
    return plan