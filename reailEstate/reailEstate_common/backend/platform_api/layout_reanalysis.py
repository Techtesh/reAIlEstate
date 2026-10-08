"""Reviewer-defined layout crops and narrow, checksum-bound AI evaluation."""

import hashlib
import time
from uuid import uuid4

from django.db import OperationalError, connection, models, transaction
from django.utils import timezone

from .artifact_storage import storage
from .floor_plan_agent import FloorPlanAgent
from .floor_plan_visibility import HIDDEN_REVIEW_STATES
from .floor_plan_vision import get_floor_plan_vision_provider, resolve_vision_selection
from .integrations import ProviderUnavailable
from .models import AuditEvent, FloorPlan, FloorPlanAsset, Operation, OutboxMessage, ReviewTask


CROP_EVENT = "floor_plan.crop_evaluation.requested"
TERMINAL_CROP_ERRORS = frozenset({
    "MANUAL_CROP_NOT_SINGLE_LAYOUT", "STALE_LAYOUT_ASSET", "IMAGE_INTEGRITY_MISMATCH",
    "LAYOUT_REMOVED", "LAYOUT_ASSET_NOT_READY", "PROJECT_NOT_ACTIVE",
})


def layout_evaluation_error(exc):
    """Only allow known domain messages into persisted errors, not provider text."""
    if isinstance(exc, ValueError) and str(exc) in TERMINAL_CROP_ERRORS:
        return str(exc)
    if isinstance(exc, ProviderUnavailable):
        return "PROVIDER_UNAVAILABLE"
    return type(exc).__name__[:64]


def _locked_layout(plan_id):
    # SQLite ignores SELECT FOR UPDATE. Reserve its writer BEFORE the first read,
    # avoiding read-snapshot upgrades for simultaneous enqueue requests. This is
    # a no-op: aliases, crop revisions and updated_at are not touched. Nested crop
    # updates already own the writer; no file/document lock is ever acquired.
    if connection.vendor == "sqlite":
        FloorPlan.objects.filter(pk=plan_id).update(id=models.F("id"))
    plan = FloorPlan.objects.select_for_update().get(pk=plan_id)
    if plan.asset_id:
        plan.asset = FloorPlanAsset.objects.select_for_update().get(pk=plan.asset_id)
    return plan


def _validate_layout(plan, payload=None):
    if plan.crop_remaining:
        raise ValueError("LAYOUT_CROP_REQUIRED")
    if plan.review_state in HIDDEN_REVIEW_STATES:
        raise ValueError("LAYOUT_REMOVED")
    if plan.project.status == "retired":
        raise ValueError("PROJECT_NOT_ACTIVE")
    from .policies.crops import crop_destination
    from .project_lifecycle import project_family_ids
    try:
        destination = crop_destination(plan.project)
    except ValueError:
        raise ValueError("PROJECT_NOT_ACTIVE") from None
    family = project_family_ids(destination)
    if payload is not None and (
        not plan.asset_id or str(plan.asset_id) != payload.get("assetId")
        or plan.asset.sha256 != payload.get("assetSha256")
    ):
        raise ValueError("STALE_LAYOUT_ASSET")
    if not plan.asset_id or plan.asset.status != "ready" or plan.document.scan_status != "clean":
        raise ValueError("LAYOUT_ASSET_NOT_READY")
    if plan.asset.document_id != plan.document_id:
        raise ValueError("STALE_LAYOUT_ASSET")
    if plan.asset.project_id not in family or plan.document.project_id not in family:
        raise ValueError("STALE_LAYOUT_ASSET")


def queue_layout_evaluation(plan, actor=None, vision_profile_id=None, vision_selection=None, *, retry=False, force=False):
    """Return (message, operation), idempotent for plan/asset/checksum.

    No image I/O, model work or document ownership. Pending/running (including
    backoff) and completed jobs are reused. retry=True re-arms failed jobs using
    their original pin; force=True creates a new job after completion only.
    Neither flag restarts cancelled jobs. Safe inside a crop-update transaction.
    SQLite lock retries restart only our own transaction, never a caller's.
    """
    nested = connection.in_atomic_block
    for attempt in range(4):
        try:
            with transaction.atomic():
                return _queue_layout_evaluation(plan.pk, actor, vision_profile_id, vision_selection, retry, force)
        except OperationalError as exc:
            if (nested or connection.vendor != "sqlite" or attempt == 3
                    or str(exc) not in {"database is locked", "database table is locked"}):
                raise
            time.sleep(0.05 * (attempt + 1))


def _queue_layout_evaluation(plan_id, actor, vision_profile_id, vision_selection, retry, force):
    plan = _locked_layout(plan_id)
    _validate_layout(plan)
    identity = {"floorPlanId": str(plan.pk), "assetId": str(plan.asset_id), "assetSha256": plan.asset.sha256}
    jobs = list(OutboxMessage.objects.select_for_update().filter(
        event_type=CROP_EVENT, payload__floorPlanId=str(plan.pk),
    ).order_by("-occurred_at", "-pk"))
    existing = None
    for job in jobs:
        operation = Operation.objects.select_for_update().get(pk=job.payload["operationId"])
        if all(job.payload.get(key) == value for key, value in identity.items()):
            if existing is None:
                existing = job, operation
        elif not job.published_at and operation.state not in {"completed", "cancelled"}:
            # Never cancel by asset: sibling plan records may share that image.
            operation.state = "cancelled"
            operation.error_code = "STALE_LAYOUT_ASSET"
            operation.save(update_fields=["state", "error_code", "updated_at"])
            job.dead_lettered_at = timezone.now()
            job.last_error_code = "STALE_LAYOUT_ASSET"
            job.save(update_fields=["dead_lettered_at", "last_error_code"])
    if existing:
        message, operation = existing
        if retry and operation.state == "failed" and not message.published_at:
            message.dead_lettered_at = None
            message.available_after = None
            message.last_error_code = ""
            message.attempts = 0
            message.save(update_fields=["dead_lettered_at", "available_after", "last_error_code", "attempts"])
            operation.state = "pending"
            operation.error_code = ""
            operation.result = {key: value for key, value in operation.result.items()
                                if key not in {"failureCategory", "retryable"}}
            operation.save(update_fields=["state", "error_code", "result", "updated_at"])
            AuditEvent.objects.create(
                actor=actor, action="floor_plan.crop_evaluation_retried", entity_type="FloorPlan",
                entity_id=plan.pk, correlation_id=message.correlation_id,
                metadata={"messageId": str(message.pk), "operationId": str(operation.pk)},
            )
        if not (force and operation.state == "completed"):
            return message, operation
    selection = vision_selection if vision_selection is not None else resolve_vision_selection(vision_profile_id)
    operation = Operation.objects.create(
        owner=actor, kind="floor_plan_crop_evaluation", state="pending",
        result=identity,
    )
    correlation_id = uuid4()
    message = OutboxMessage.objects.create(
        event_type=CROP_EVENT,
        payload={
            **identity, "visionSelection": selection,
            "operationId": str(operation.id),
        },
        correlation_id=correlation_id,
    )
    AuditEvent.objects.create(
        actor=actor, action="floor_plan.crop_evaluation_queued", entity_type="FloorPlan",
        entity_id=plan.id, correlation_id=correlation_id,
        metadata={"assetId": str(plan.asset_id), "operationId": str(operation.id)},
    )
    return message, operation


def create_manual_layout_crops(
    source_plan, raw_regions, actor=None, vision_profile_id=None, queue=True, rotation_degrees=0,
):
    # Legacy arguments remain accepted, but Save no longer publishes or queues crops.
    # Evaluation is selected explicitly when the user adds drafts to the project.
    from .crop_cart import stage_crops
    return stage_crops(source_plan, raw_regions, actor, rotation_degrees)


def evaluate_layout_crop(payload):
    """Worker only; caller owns the document lock, never a database transaction."""
    if connection.in_atomic_block:
        raise RuntimeError("Layout evaluation must run outside database transactions.")
    provider = None
    try:
        with transaction.atomic():
            plan = _locked_layout(payload["floorPlanId"])
            operation = Operation.objects.select_for_update().get(pk=payload["operationId"])
            if _evaluation_stopped(plan, operation):
                return plan
            _validate_layout(plan, payload)
            operation.state = "running"
            operation.error_code = ""
            operation.save(update_fields=["state", "error_code", "updated_at"])
        asset = plan.asset
        with storage.open(asset.object_key, "rb") as stream:
            data = stream.read(asset.byte_length + 1)
        if len(data) != asset.byte_length or hashlib.sha256(data).hexdigest() != payload["assetSha256"]:
            raise ValueError("IMAGE_INTEGRITY_MISMATCH")
        with transaction.atomic():
            plan = _locked_layout(payload["floorPlanId"])
            operation = Operation.objects.select_for_update().get(pk=payload["operationId"])
            if _evaluation_stopped(plan, operation):
                return plan
            _validate_layout(plan, payload)
        provider = get_floor_plan_vision_provider(payload.get("visionSelection"))
        agent = FloorPlanAgent(provider)
        result = provider.analyze_layout(data, asset.media_type, agent._vision_schema())
        candidates = agent._reconcile(agent._validate(agent._vision_candidates(asset.page_number, result, asset)))
        flat_candidates = [candidate for candidate in candidates if candidate.plan_scope == "flat"]
        if len(flat_candidates) != 1:
            raise ValueError("MANUAL_CROP_NOT_SINGLE_LAYOUT")
        candidate = flat_candidates[0]
        with transaction.atomic():
            plan = _locked_layout(payload["floorPlanId"])
            operation = Operation.objects.select_for_update().get(pk=payload["operationId"])
            if _evaluation_stopped(plan, operation):
                return plan
            _validate_layout(plan, payload)
            # Keep the reviewer label and unique draft suffix; model names often collide.
            if not plan.raw_extraction.get("draftId") and not plan.raw_extraction.get("segmentationMethod"):
                plan.plan_name = candidate.plan_name or plan.plan_name
            plan.configuration_type = candidate.configuration_type
            plan.carpet_area_sqft = candidate.carpet_area_sqft
            plan.room_count = agent._room_count(candidate.configuration_type)
            plan.bathroom_count = sum(1 for item in candidate.dimensions if item["room"].startswith(("bath", "toilet"))) or None
            plan.balcony_count = sum(1 for item in candidate.dimensions if item["room"].startswith("balcony")) or None
            plan.orientation = candidate.orientation
            plan.dimensions = candidate.dimensions
            plan.features = candidate.features or {}
            plan.raw_extraction = plan.raw_extraction | {
                "cropEvaluation": {"pageType": result["pageType"], "visualEvidence": result["visualEvidence"],
                                   "assetId": payload["assetId"], "assetSha256": payload["assetSha256"],
                                   **agent._provider_metadata()},
            }
            if plan.raw_extraction.get("extractionAssetId") == payload["assetId"] and plan.raw_extraction.get("extractionAssetSha256") == payload["assetSha256"]:
                plan.raw_extraction["extractionStale"] = False
            plan.confidence = candidate.confidence
            plan.review_state = "needs_boundary_review" if plan.raw_extraction.get("qualityWarning") else "gen_with_ai_needs_review"
            plan.extractor_version = agent.version
            plan.save()
            ReviewTask.objects.get_or_create(
                project=plan.project, task_type="floor_plan", state="open",
                resolution={"floorPlanId": str(plan.id)}, defaults={"priority": 1},
            )
            operation.state = "completed"
            operation.error_code = ""
            operation.result = {key: value for key, value in operation.result.items()
                                if key not in {"failureCategory", "retryable"}}
            operation.result = operation.result | {"reviewState": plan.review_state, "completedAt": timezone.now().isoformat()}
            operation.save(update_fields=["state", "error_code", "result", "updated_at"])
        return plan
    except Exception as exc:
        code = layout_evaluation_error(exc)
        with transaction.atomic():
            current = _locked_layout(payload["floorPlanId"])
            operation = Operation.objects.select_for_update().get(pk=payload["operationId"])
            if not _evaluation_stopped(current, operation):
                # A failure from an obsolete model request must not even mark the
                # replacement crop bad or reset its stale/revision metadata.
                try:
                    _validate_layout(current, payload)
                except ValueError as validation_error:
                    code = layout_evaluation_error(validation_error)
                if code == "MANUAL_CROP_NOT_SINGLE_LAYOUT":
                    current.review_state = "needs_boundary_review"
                    current.raw_extraction = current.raw_extraction | {
                        "qualityWarning": "Crop evaluation did not find exactly one dwelling. Correct the boundary from the original floor page."
                    }
                    current.save(update_fields=["review_state", "raw_extraction", "updated_at"])
                    operation.result = operation.result | {"failureCategory": "BAD_BOUNDARY", "retryable": False}
                operation.state = "cancelled" if code == "STALE_LAYOUT_ASSET" else "failed"
                operation.error_code = code
                operation.save(update_fields=["state", "error_code", "result", "updated_at"])
        raise
    finally:
        if hasattr(provider, "close"):
            provider.close()


def _evaluation_stopped(plan, operation):
    if operation.state in {"completed", "cancelled"}:
        return True
    if plan.review_state in HIDDEN_REVIEW_STATES or plan.project.status == "retired":
        operation.state = "cancelled"
        operation.error_code = "LAYOUT_REMOVED" if plan.review_state in HIDDEN_REVIEW_STATES else "PROJECT_NOT_ACTIVE"
        operation.save(update_fields=["state", "error_code", "updated_at"])
        return True
    return False
