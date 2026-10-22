"""Persist activity changes without deleting evidence or reactivating child layouts."""

from uuid import uuid4

from django.db import transaction

from .floor_plan_visibility import HIDDEN_REVIEW_STATES
from .models import AuditEvent, FloorPlan, FloorPlanAsset


def derived_layout_ids(source, plans, assets):
    """Prefer recorded plan/asset ancestry; use a page fallback only for a sole source."""
    floors = [plan for plan in plans if plan.plan_scope == "floor"
              and plan.review_state not in HIDDEN_REVIEW_STATES
              and plan.page_number == source.page_number]
    owners = {}
    for floor in floors:
        if floor.asset_id:
            owners.setdefault(str(floor.asset_id), set()).add(str(floor.pk))
    sources = {str(asset.pk): asset for asset in assets}
    result = []
    for plan in plans:
        if (plan.plan_scope != "flat" or plan.page_number != source.page_number
                or plan.review_state in HIDDEN_REVIEW_STATES):
            continue
        raw = plan.raw_extraction if isinstance(plan.raw_extraction, dict) else {}
        box = raw.get("boundingBox", {})
        hints = [raw, box if isinstance(box, dict) else {}]
        current = str(plan.asset_id)
        seen = set()
        matched = None
        while current in sources and current not in seen:
            seen.add(current)
            asset = sources[current]
            if asset.page_number and source.page_number and asset.page_number != source.page_number:
                matched = False
                break
            bounds = asset.source_bounds if isinstance(asset.source_bounds, dict) else {}
            hints = [bounds, *hints]
            source_plan_id = next((str(hint["sourcePlanId"]) for hint in hints if hint.get("sourcePlanId")), None)
            if source_plan_id:
                matched = source_plan_id == str(source.pk)
                break
            if current in owners:
                matched = owners[current] == {str(source.pk)}
                break
            current = next((str(hint["sourceAssetId"]) for hint in hints if hint.get("sourceAssetId")), "")
            hints = []
        if matched is None:
            source_plan_id = next((str(hint["sourcePlanId"]) for hint in hints if hint.get("sourcePlanId")), None)
            if source_plan_id:
                matched = source_plan_id == str(source.pk)
            else:
                matched = (not current or not plan.asset_id) and len(floors) == 1 and floors[0].pk == source.pk
        if matched:
            result.append(plan.pk)
    return result


@transaction.atomic
def set_plan_active_locked(plan, active, actor, *, require_review=False):
    """Call after authorizing and version-checking the locked selected plan."""
    changed = [plan]
    if not active and plan.plan_scope == "floor":
        plans = list(FloorPlan.objects.filter(
            project_id=plan.project_id, document_id=plan.document_id,
        ).order_by("pk"))
        assets = FloorPlanAsset.objects.filter(project_id=plan.project_id, document_id=plan.document_id)
        child_ids = set(derived_layout_ids(plan, plans, assets))
        changed.extend(FloorPlan.objects.select_for_update(of=("self",)).filter(
            pk__in=child_ids, project_id=plan.project_id, document_id=plan.document_id,
            plan_scope="flat", active=True,
        ).order_by("pk"))
    for target in changed:
        target.active = active
        fields = ["active", "updated_at"]
        if require_review and target.review_state == "approved":
            target.review_state = "needs_review"
            fields.append("review_state")
        target.save(update_fields=fields)
        metadata = {"active": active}
        if target.pk != plan.pk:
            metadata["sourceFloorPlanId"] = str(plan.pk)
        AuditEvent.objects.create(actor=actor, action="floor_plan.active_changed",
                                 entity_type="FloorPlan", entity_id=target.pk,
                                 correlation_id=uuid4(), metadata=metadata)
    return changed
