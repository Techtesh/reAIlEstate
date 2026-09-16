"""Presentation and enqueue-only single-layout requests over durable jobs."""

from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from .floor_plan_visibility import visible_floor_plans
from .models import FloorPlan, FloorPlanAsset, Operation, OutboxMessage, Project
from .project_lifecycle import project_family_ids

CROP_EVENT = "floor_plan.crop_evaluation.requested"
PAGE_EVENT = "source.page_evaluation.requested"


def layout_evaluation_states(plans):
    """Bulk reads only; never present a previous image's job as current extraction."""
    plans = list(plans)
    ids = [str(plan.pk) for plan in plans]
    messages = list(OutboxMessage.objects.filter(
        event_type__in=[CROP_EVENT, PAGE_EVENT], payload__floorPlanId__in=ids,
    ).order_by("occurred_at", "pk"))
    operations = {str(op.pk): op for op in Operation.objects.filter(
        pk__in=[job.payload["operationId"] for job in messages if job.payload.get("operationId")]
    )}
    checksums = {str(asset.pk): asset.sha256 for asset in FloorPlanAsset.objects.filter(
        pk__in=[plan.asset_id for plan in plans if plan.asset_id],
    ).only("pk", "sha256")}
    plan_assets = {str(plan.pk): str(plan.asset_id) for plan in plans}
    latest = {job.payload["floorPlanId"]: job for job in messages
              if job.payload.get("assetId") == plan_assets[job.payload["floorPlanId"]]
              and job.payload.get("assetSha256") == checksums.get(job.payload.get("assetId"))}
    historical_ids = {job.payload["floorPlanId"] for job in messages}
    states = {}
    for plan in plans:
        job = latest.get(str(plan.pk))
        op = operations.get(job.payload.get("operationId")) if job else None
        state = op.state if op else "not_queued"
        if plan.crop_remaining:
            states[str(plan.pk)] = {"state": "crop_remaining", "label": "Crop remaining", "canRun": False}
            continue
        if job and not op:
            state = "completed" if job.published_at else "failed" if job.dead_lettered_at else "pending"
        if not job and str(plan.pk) not in historical_ids and (plan.dimensions or plan.raw_extraction.get("cropEvaluation")):
            state = "completed"
        if state in {"pending", "queued"}:
            state = "queued"
        labels = {"queued": "Queued — extraction has not run yet",
                  "running": "Extracting — refresh to see results",
                  "failed": "Extraction failed — inspect the error or retry",
                  "cancelled": "Extraction cancelled",
                  "completed": "Extraction completed — awaiting review",
                  "not_queued": "No page/crop extraction job found"}
        if state == "completed" and not plan.dimensions:
            label = "Extraction completed, but no readable room dimensions were found"
        else:
            label = labels.get(state, state)
        states[str(plan.pk)] = {
            "state": state, "label": label, "attempts": job.attempts if job else 0,
            "error": (op.error_code if op else "") or (job.last_error_code if job else ""),
            "retryAt": job.available_after if job else None,
            "canRun": plan.plan_scope == "flat" and state not in {"running", "cancelled"},
            "button": "Requeue failed extraction" if state == "failed" else "Queue extraction",
        }
    return states


def queue_single_layout(project, plan_id, actor, *, retry=False, force=False):
    """Authorize within the dashboard family and return queue status, never work.

    Staff access is global, but both project access and floor-plan change
    permission are required. A submitted plan ID cannot escape this family.
    """
    if (not actor or not actor.is_active or not actor.is_staff
            or not actor.has_perm("platform_api.change_floorplan")
            or not (actor.has_perm("platform_api.view_project") or actor.has_perm("platform_api.change_project"))):
        raise PermissionDenied
    project = get_object_or_404(Project, pk=project.pk)
    plan = get_object_or_404(visible_floor_plans(FloorPlan.objects.filter(
        project_id__in=project_family_ids(project), plan_scope="flat")), pk=plan_id)
    if project.status in {"retired", "merged"} or plan.project.status == "retired":
        raise ValueError("PROJECT_NOT_ACTIVE")
    from .layout_reanalysis import queue_layout_evaluation
    job, operation = queue_layout_evaluation(plan, actor=actor, retry=retry, force=force)
    status = "queued" if operation.state in {"pending", "queued"} else operation.state
    return {
        "status": status, "messageId": str(job.pk), "operationId": str(operation.pk),
        "floorPlanId": str(plan.pk), "errorCode": operation.error_code or job.last_error_code,
        "retryAt": job.available_after,
    }


def run_single_layout(project, plan_id, actor, *, retry=False, force=False):
    """Compatibility name only: this function QUEUES, it never executes a worker."""
    return queue_single_layout(project, plan_id, actor, retry=retry, force=force)