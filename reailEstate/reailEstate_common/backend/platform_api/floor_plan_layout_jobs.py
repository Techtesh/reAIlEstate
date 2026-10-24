"""Durable, checksum-bound full-floor to individual-layout evaluation.

Queueing is safe inside the source-page worker's document work lock: only short
row locks are acquired here. Model calls belong exclusively to the worker and
never run inside a database transaction. Completed/cancelled jobs are retained.
"""

import hashlib
from dataclasses import asdict
from uuid import uuid4

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from .artifact_storage import storage
from .feature_flags import FeatureDisabled, require_enabled
from .floor_plan_vision import get_floor_plan_vision_provider, resolve_ai_vision_selection, provider_metadata
from .ingestion_runtime import work_lock
from .models import AuditEvent, EvidenceDocument, FloorPlan, FloorPlanAsset, Operation, OutboxMessage, Project
from .policies.crops import crop_destination
from .unit_segmentation import replace_unit_segments, require_segment_replacement_allowed, validate_regions


UNITS_EVENT = "floor_plan.units_evaluation.requested"
OPERATION_KIND = "floor_plan_units_evaluation"
HIDDEN_STATES = frozenset({"removed_by_user", "replaced_by_user", "superseded_page_candidate"})


def _authorize(actor, project=None):
    from .builder_access import builder_can_manage
    if not actor or not actor.is_active:
        raise PermissionDenied
    if actor.is_staff and actor.has_perm("platform_api.change_floorplan"):
        return
    if actor.is_staff or project is None or not builder_can_manage(actor, project):
        raise PermissionDenied


def _selection(vision_profile_id=None, vision_selection=None):
    """Accept only a server profile snapshot, never endpoints or credentials."""
    if vision_selection is None:
        return resolve_ai_vision_selection(vision_profile_id)
    if not isinstance(vision_selection, dict) or set(vision_selection) != {"profileId", "provider", "model", "configurationHash"}:
        raise ValueError("INVALID_VISION_SELECTION")
    if vision_profile_id is not None and vision_profile_id != vision_selection["profileId"]:
        raise ValueError("INVALID_VISION_SELECTION")
    return dict(vision_selection)


def _locked_source(plan_id, document_id):
    # Do not SELECT FOR UPDATE through the nullable asset join (PostgreSQL).
    document = EvidenceDocument.objects.select_for_update().get(pk=document_id)
    plan = FloorPlan.objects.select_for_update().get(pk=plan_id)
    if plan.document_id != document.pk:
        raise ValueError("STALE_FLOOR_PLAN_SOURCE")
    plan.document = document
    plan.project = Project.objects.select_for_update().get(pk=plan.project_id)
    if plan.asset_id:
        plan.asset = FloorPlanAsset.objects.select_for_update().get(pk=plan.asset_id)
    return plan


def _record_floor_observations(plan, provider, metadata, regions):
    """Keep source observations separate from successful layout crop creation."""
    observation = {
        **provider_metadata(provider), "pageType": metadata.get("pageType", "uncertain"),
        "flatsPerFloor": len(regions) if regions and metadata.get("pageType") == "multi_unit_floor_plan" else None,
        "liftCount": metadata.get("liftCount"), "staircaseCount": metadata.get("staircaseCount"),
        "refugeArea": metadata.get("refugeArea", {"presence": "unknown", "area": None, "unit": None, "evidence": ""}),
        "commonAreas": metadata.get("commonAreas", []),
        "evaluatedAt": timezone.now().isoformat(), "visualEvidence": metadata.get("visualEvidence", ""),
        "assetId": str(plan.asset_id), "assetSha256": plan.asset.sha256,
    }
    plan.features = {**plan.features, "floorEvaluation": observation}
    plan.review_state = "needs_review"
    plan.save(update_fields=["features", "review_state", "updated_at"])
    return observation


def _validate_source(plan, payload=None):
    if plan.review_state in HIDDEN_STATES:
        raise ValueError("FLOOR_PLAN_SOURCE_HIDDEN")
    if plan.project.status in {"retired", "merged"}:
        raise ValueError("PROJECT_NOT_ACTIVE")
    asset = plan.asset
    if plan.plan_scope != "floor" or (asset and asset.asset_type == "unit_crop"):
        raise ValueError("FULL_FLOOR_SOURCE_REQUIRED")
    if asset is None or asset.status != "ready" or plan.document.scan_status != "clean":
        raise ValueError("SOURCE_ASSET_NOT_READY")
    if asset.document_id != plan.document_id or asset.project_id != plan.project_id:
        raise ValueError("STALE_FLOOR_PLAN_SOURCE")
    if payload is not None and (
        str(plan.id) != payload["sourcePlanId"] or str(plan.id) != payload["floorPlanId"]
        or str(plan.project_id) != payload["projectId"]
        or str(plan.document_id) != payload["documentId"]
        or str(asset.id) != payload["assetId"] or asset.sha256 != payload["assetSha256"]
    ):
        raise ValueError("STALE_FLOOR_PLAN_SOURCE")


@transaction.atomic
def queue_floor_plan_layouts(plan, actor, vision_profile_id=None, vision_selection=None, *, retry=False, fresh=False):
    """Return the new/existing OutboxMessage for this source ID and checksum.

    ``vision_selection`` must be an already server-resolved AI snapshot. It is
    validated, not replaced with today's default. Existing jobs keep their pin
    while that profile is still configured; an unavailable pin creates a fresh
    attempt with the current profile.
    ``retry=True`` re-arms a failed dead letter in place; completed and cancelled
    operations are never restarted. ``fresh=True`` creates a new attempt using
    current AI settings after terminal failure/completion, preserving history.
    Pending/running/backoff and cancelled work is reused. No image/model I/O.
    """
    _authorize(actor, plan.project)
    plan = _locked_source(plan.pk, plan.document_id)
    _validate_source(plan)
    _authorize(actor, plan.project)
    require_enabled("media.auto_segment", actor=actor, project=crop_destination(plan.project))
    existing = OutboxMessage.objects.select_for_update().filter(
        event_type=UNITS_EVENT, payload__sourcePlanId=str(plan.pk),
        payload__assetSha256=plan.asset.sha256,
    ).order_by("-occurred_at", "-pk").first()
    if existing:
        operation = Operation.objects.select_for_update().get(pk=existing.payload["operationId"])
        if (retry and not fresh and existing.dead_lettered_at and not existing.published_at
                and operation.state == "failed"):
            # Explicit retry must still honor the original profile, even if the
            # caller selected another profile or the server default has changed.
            try:
                _selection(vision_selection=existing.payload["visionSelection"])
            except ValueError:
                fresh = True
            else:
                existing.dead_lettered_at = None
                existing.available_after = None
                existing.last_error_code = ""
                existing.attempts = 0
                existing.save(update_fields=["dead_lettered_at", "available_after", "last_error_code", "attempts"])
                operation.state = "pending"
                operation.error_code = ""
                operation.save(update_fields=["state", "error_code", "updated_at"])
                AuditEvent.objects.create(
                    actor=actor, action="floor_plan.units_evaluation_retried", entity_type="FloorPlan",
                    entity_id=plan.pk, correlation_id=existing.correlation_id,
                    metadata={"operationId": str(operation.pk), "messageId": str(existing.pk)},
                )
        new_attempt = (fresh and (operation.state == "completed" or (
            operation.state == "failed" and (existing.dead_lettered_at or existing.published_at))))
        if not new_attempt:
            return existing
    selection = _selection(vision_profile_id, None if fresh else vision_selection)
    result = {"sourcePlanId": str(plan.pk), "floorPlanId": str(plan.pk),
              "assetId": str(plan.asset_id), "assetSha256": plan.asset.sha256}
    operation = Operation.objects.create(owner=actor, kind=OPERATION_KIND, state="pending", result=result)
    message = OutboxMessage.objects.create(
        event_type=UNITS_EVENT, correlation_id=uuid4(),
        payload={**result, "projectId": str(plan.project_id), "documentId": str(plan.document_id),
                 "operationId": str(operation.pk), "visionSelection": selection},
    )
    AuditEvent.objects.create(
        actor=actor, action="floor_plan.units_evaluation_queued", entity_type="FloorPlan",
        entity_id=plan.pk, correlation_id=message.correlation_id,
        metadata={**result, "operationId": str(operation.pk), "messageId": str(message.pk)},
    )
    return message


def _stopped(operation, plan):
    if operation.state in {"completed", "cancelled"}:
        return True
    if plan.review_state in HIDDEN_STATES or plan.project.status in {"retired", "merged"}:
        operation.state = "cancelled"
        operation.error_code = "FLOOR_PLAN_SOURCE_INACTIVE"
        operation.save(update_fields=["state", "error_code", "updated_at"])
        return True
    return False


def _operation(payload):
    operation = Operation.objects.select_for_update().get(pk=payload["operationId"])
    if operation.kind != OPERATION_KIND or any(
        operation.result.get(key) != payload[key]
        for key in ("sourcePlanId", "floorPlanId", "assetId", "assetSha256")
    ):
        raise ValueError("INVALID_FLOOR_PLAN_OPERATION")
    return operation


def evaluate_floor_plan_layouts(payload):
    """Process one units event under its document lock; return its Operation.

    Crops, their evaluation messages, and operation completion commit together.
    A replay after commit returns without model calls or replacement. The full
    floor source is never superseded. Invoke outside any database transaction.
    """
    with work_lock("document", payload["documentId"]):
        provider = None
        operation = None
        saved_keys = []
        try:
            with transaction.atomic():
                plan = _locked_source(payload["sourcePlanId"], payload["documentId"])
                operation = _operation(payload)
                if _stopped(operation, plan):
                    return operation
                _validate_source(plan, payload)
                _authorize(operation.owner, plan.project)
                operation.state = "running"
                operation.error_code = ""
                operation.save(update_fields=["state", "error_code", "updated_at"])
            asset = plan.asset
            with storage.open(asset.object_key, "rb") as stream:
                data = stream.read(asset.byte_length + 1)
            if len(data) != asset.byte_length or hashlib.sha256(data).hexdigest() != payload["assetSha256"]:
                raise ValueError("IMAGE_INTEGRITY_MISMATCH")
            selection = _selection(vision_selection=payload["visionSelection"])
            with transaction.atomic():
                # Image I/O and profile resolution may take time. Check again
                # immediately before spending a model request, not just at claim.
                plan = _locked_source(payload["sourcePlanId"], payload["documentId"])
                operation = _operation(payload)
                if _stopped(operation, plan):
                    return operation
                _validate_source(plan, payload)
                _authorize(operation.owner, plan.project)
            require_segment_replacement_allowed(plan, ["ai"], operation.owner)
            provider = get_floor_plan_vision_provider(selection)
            regions, metadata = provider.analyze_full_floor(data, asset.media_type)
            # The provider returns UnitRegion instances, not raw response dicts.
            regions = validate_regions([asdict(region) for region in regions])
            if (not regions or metadata.get("pageType") not in {"multi_unit_floor_plan", "single_unit_layout", "layout_sheet"}
                    or metadata.get("unitCount") != len(regions)):
                if metadata.get("unitCount") == len(regions):
                    with transaction.atomic():
                        plan = _locked_source(payload["sourcePlanId"], payload["documentId"])
                        operation = _operation(payload)
                        if _stopped(operation, plan):
                            return operation
                        _validate_source(plan, payload)
                        _authorize(operation.owner, plan.project)
                        _record_floor_observations(plan, provider, metadata, regions)
                raise ValueError("NO_INDIVIDUAL_LAYOUTS_DETECTED")
            with transaction.atomic():
                # Reload rather than saving stale pre-model instances: removal,
                # replacement, cancellation or changed checksums must win.
                plan = _locked_source(payload["sourcePlanId"], payload["documentId"])
                operation = _operation(payload)
                if _stopped(operation, plan):
                    return operation
                _validate_source(plan, payload)
                _authorize(operation.owner, plan.project)
                floor_evaluation = _record_floor_observations(plan, provider, metadata, regions)
                plans, workflow = replace_unit_segments(
                    plan, regions, "ai", metadata={**metadata, "visionSelection": selection,
                                                  "operationId": str(operation.pk)},
                    actor=operation.owner,
                )
                saved_keys = [crop.asset.object_key for crop in plans]
                operation.state = "completed"
                operation.error_code = ""
                operation.result = operation.result | {
                    **provider_metadata(provider),
                    "workflowRunId": str(workflow.pk), "planIds": [str(crop.pk) for crop in plans],
                    "cropJobIds": workflow.result["cropJobIds"], "unitCount": len(plans), "sourceRetained": True,
                    "floorEvaluation": floor_evaluation,
                    "completedAt": timezone.now().isoformat(),
                }
                operation.save(update_fields=["state", "error_code", "result", "updated_at"])
            return operation
        except Exception as exc:
            # replace_unit_segments cleans up its own failed writes. If the
            # outer transaction fails later (e.g. crop enqueue), clean those
            # new files too, but never delete any committed/referenced asset.
            if operation is not None:
                from .layout_reanalysis import layout_evaluation_error
                Operation.objects.filter(pk=operation.pk).exclude(state__in=["completed", "cancelled"]).update(
                    state="failed", error_code=exc.code if isinstance(exc, FeatureDisabled) else layout_evaluation_error(exc),
                    updated_at=timezone.now(),
                )
            for key in saved_keys:
                if not FloorPlanAsset.objects.filter(object_key=key).exists():
                    storage.delete(key)
            raise
        finally:
            if provider is not None and hasattr(provider, "close"):
                provider.close()
