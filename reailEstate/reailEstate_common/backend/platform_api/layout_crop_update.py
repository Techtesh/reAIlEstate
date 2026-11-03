"""Identity-preserving crop edits. No inference, draft creation or source mutation.

The signed editor state pins both the current layout and selected preview. Image
I/O owns the document work lock, never a DB transaction. Persistence repeats the
version/authorization checks under row locks, then enqueues checksum-bound work.
"""

import hashlib
import io
import json
import math
from contextlib import ExitStack
from copy import deepcopy
from uuid import UUID, uuid4

from PIL import Image
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.utils import timezone

from .artifact_storage import storage
from .feature_flags import require_enabled
from .policies.crops import crop_destination
from .floor_plan_visibility import HIDDEN_REVIEW_STATES
from .ingestion_runtime import work_lock
from .models import (
    AgentWorkflowRun, AuditEvent, EvidenceDocument, FloorPlan, FloorPlanAsset,
    IngestionRun, Operation, OutboxMessage, Project, ReviewTask,
)
from .project_lifecycle import project_family_ids
from .source_page_additions import render_source_pages

VERSION = "layout-crop-update-1.0"
TOKEN_SALT = "platform_api.layout_crop_update"
TOKEN_MAX_AGE = 3600
MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
JOB_EVENTS = (
    "floor_plan.crop_evaluation.requested", "source.page_evaluation.requested",
    "floor_plan.units_evaluation.requested",
)
FACT_DEFAULTS = {
    "configuration_type": "", "carpet_area_sqft": None, "room_count": None,
    "bathroom_count": None, "balcony_count": None, "orientation": "",
    "dimensions": [], "features": {}, "confidence": 0,
    "review_state": "manual_crop_needs_evaluation", "extractor_version": VERSION,
}


class StaleCropEdit(ValueError):
    """The editor no longer describes the current image or extraction."""


def validate_rectangle(regions):
    """One normalized finite rectangle, including full-range/near-full trims."""
    if not isinstance(regions, list) or len(regions) != 1 or not isinstance(regions[0], dict):
        raise ValueError("Deliberately select exactly one rectangle.")
    region = regions[0]
    values = [region.get(key) for key in ("x", "y", "width", "height")]
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        raise ValueError("Rectangle coordinates must be finite numbers.")
    x, y, width, height = values
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < width <= 1 and 0 < height <= 1
            and x + width <= 1 and y + height <= 1):
        raise ValueError("Keep the rectangle inside the image with positive width and height.")
    return dict(zip(("x", "y", "width", "height"), values))


def validate_rotation(rotation):
    if type(rotation) is not int or not 0 <= rotation < 360 or rotation % 5:
        raise ValueError("Rotation must be a multiple of 5 degrees from 0 to 355 clockwise.")
    return rotation


def _staff(actor):
    # Same global staff/project permission policy as queue_single_layout.
    if (not actor or not actor.is_active or not actor.is_staff
            or not actor.has_perm("platform_api.change_floorplan")
            or not (actor.has_perm("platform_api.view_project")
                    or actor.has_perm("platform_api.change_project"))):
        raise PermissionDenied


def editable_layout(plan_id, actor, project_id=None):
    """Authorize the actual target and its canonical family, not a posted source."""
    _staff(actor)
    plan = FloorPlan.objects.select_related("project", "document", "asset").get(pk=plan_id)
    destination = crop_destination(plan.project)
    if project_id is not None and str(destination.pk) != str(project_id):
        raise PermissionDenied
    family = project_family_ids(destination)
    if (plan.project_id not in family or plan.document.project_id not in family
            or plan.document.project.organization_id != destination.organization_id
            or plan.document.project.status == "retired"
            or plan.plan_scope not in {"flat", "floor"} or plan.review_state in HIDDEN_REVIEW_STATES
            or not plan.asset_id or plan.asset.status != "ready"
            or plan.document.scan_status != "clean"
            or plan.asset.document_id != plan.document_id or plan.asset.project_id not in family
            or plan.asset.project.organization_id != destination.organization_id
            or plan.asset.project.status == "retired"):
        raise ValueError("Choose an active, clean, ready layout in this project family.")
    return plan, destination


def _version(plan, destination):
    return {
        "plan_id": str(plan.pk), "project_id": str(destination.pk),
        "expected_asset_id": str(plan.asset_id), "expected_sha256": plan.asset.sha256,
        "updated_at": plan.updated_at.isoformat(),
        "document_id": str(plan.document_id), "document_sha256": plan.document.sha256,
        "asset_updated_at": plan.asset.updated_at.isoformat(),
    }


def _check_version(plan, destination, state):
    if any(state.get(key) != value for key, value in _version(plan, destination).items()):
        raise StaleCropEdit("This layout or source changed. Reload the crop editor before saving.")


def _reference(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid original-source provenance; repair it before editing.") from None


def _source(plan, destination, mode):
    """Current means exactly the saved asset; original follows verified ancestry."""
    if mode == "current":
        return plan.asset, plan.asset.page_number or plan.page_number
    if mode != "original":
        raise ValueError("Choose Current cropped unit or Original source.")
    family = project_family_ids(destination)
    asset = plan.asset
    seen = {asset.pk}
    raw = plan.raw_extraction
    box = raw.get("boundingBox", {})
    hints = [asset.source_bounds, box, raw]
    page = plan.page_number or asset.page_number
    for _ in range(32):
        for hint in hints:
            if hint.get("sourceDocumentId") and _reference(hint["sourceDocumentId"]) != plan.document_id:
                raise ValueError("Original source must belong to this layout's evidence document.")
        source_id = next((hint.get("sourceAssetId") for hint in hints if hint.get("sourceAssetId")), None)
        if not source_id:
            if asset.asset_type == "page_render":
                return asset, asset.page_number or page
            break
        source_id = _reference(source_id)
        if source_id in seen:
            raise ValueError("Original-source provenance contains a cycle.")
        seen.add(source_id)
        ancestor = FloorPlanAsset.objects.filter(pk=source_id).select_related("project").first()
        if ancestor is None:
            break  # Missing historical image: verified original document page below.
        if (ancestor.document_id != plan.document_id or ancestor.project_id not in family
                or ancestor.project.organization_id != destination.organization_id
                or ancestor.project.status == "retired"
                or (page and ancestor.page_number and ancestor.page_number != page)):
            raise ValueError("Original asset is outside this layout's source page/project family.")
        if ancestor.status != "ready":
            break
        asset = ancestor
        page = asset.page_number or page
        hints = [asset.source_bounds]
    else:
        raise ValueError("Original-source ancestry is too deep.")
    page = box.get("pageNumber") or page
    if type(page) is not int or page < 1 or (plan.page_number and page != plan.page_number):
        raise ValueError("No verified source page is recorded for this layout.")
    return None, page


def _read_asset(asset):
    if not 0 < asset.byte_length <= MAX_IMAGE_BYTES:
        raise ValueError("Source image exceeds the crop editor's size limit.")
    with storage.open(asset.object_key, "rb") as stream:
        data = stream.read(asset.byte_length + 1)
    if len(data) != asset.byte_length or hashlib.sha256(data).hexdigest() != asset.sha256:
        raise ValueError("Source image checksum mismatch.")
    return data


def _image_size(data):
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Source image exceeds the crop editor's size limit.")
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise ValueError("Source image has too many pixels for the crop editor.")
        if image.format not in {"PNG", "JPEG"}:
            raise ValueError("Only PNG and JPEG crop sources are supported.")
        image.verify()
        return image.size


def _preview_data(plan, destination, mode):
    source, page = _source(plan, destination, mode)
    if source is not None:
        try:
            data = _read_asset(source)
        except FileNotFoundError:
            if mode != "original":
                raise
            source = None
    if source is None:
        # render_source_pages calls read_source, verifying actual document bytes.
        if type(page) is not int or page < 1:
            raise ValueError("No verified source page is recorded for this layout.")
        data = render_source_pages(plan.document, [page])[0][1]
    width, height = _image_size(data)
    descriptor = {
        "source_mode": mode, "source_asset_id": str(source.pk) if source else None,
        "source_sha256": hashlib.sha256(data).hexdigest(), "page_number": page,
        "width": width, "height": height,
    }
    return data, descriptor


def prepare_crop_edit(plan_id, actor, *, source_mode="current"):
    """Read-only GET preparation. Never automatically switches to the floor page."""
    if connection.in_atomic_block:
        raise RuntimeError("Crop image I/O must run outside database transactions.")
    plan, destination = editable_layout(plan_id, actor)
    version = _version(plan, destination)
    with work_lock("document", plan.document_id):
        plan, destination = editable_layout(plan_id, actor)
        _check_version(plan, destination, version)
        _, descriptor = _preview_data(plan, destination, source_mode)
        current, canonical = editable_layout(plan_id, actor)
        _check_version(current, canonical, version)
    state = {**version, **descriptor, "actor_id": str(actor.pk)}
    return plan, destination, state, signing.dumps(state, salt=TOKEN_SALT, compress=True)


def _unsign(token, plan_id, actor):
    _staff(actor)
    try:
        state = signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
    except (signing.BadSignature, TypeError):
        raise StaleCropEdit("Crop editor expired or was modified. Reload it.") from None
    if state.get("actor_id") != str(actor.pk) or state.get("plan_id") != str(plan_id):
        raise PermissionDenied
    return state


def _checked_preview(plan, destination, state):
    _check_version(plan, destination, state)
    data, descriptor = _preview_data(plan, destination, state["source_mode"])
    if any(state.get(key) != value for key, value in descriptor.items()):
        raise StaleCropEdit("Selected source changed. Reload the crop editor.")
    return data


def crop_edit_preview(plan_id, actor, token):
    if connection.in_atomic_block:
        raise RuntimeError("Crop image I/O must run outside database transactions.")
    state = _unsign(token, plan_id, actor)
    plan, destination = editable_layout(plan_id, actor, state["project_id"])
    with work_lock("document", plan.document_id):
        plan, destination = editable_layout(plan_id, actor, state["project_id"])
        data = _checked_preview(plan, destination, state)
        current, canonical = editable_layout(plan_id, actor, state["project_id"])
        _check_version(current, canonical, state)
    return data


def _render_crop(data, rectangle, rotation):
    # Coordinates describe the rotated preview. No white-canvas normalization or
    # margin expansion: a trim keeps exactly the deliberately selected pixels.
    with Image.open(io.BytesIO(data)) as original:
        image = original.convert("RGB")
    try:
        if rotation:
            rotated = image.rotate(-rotation, expand=True, resample=Image.Resampling.BICUBIC, fillcolor="white")
            image.close()
            image = rotated
        x, y, width, height = (rectangle[key] for key in ("x", "y", "width", "height"))
        box = (round(x * image.width), round(y * image.height),
               round((x + width) * image.width), round((y + height) * image.height))
        if box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError("Select a rectangle containing visible pixels.")
        with image.crop(box) as crop:
            output = io.BytesIO()
            crop.save(output, format="PNG")
            return output.getvalue(), crop.size, list(box)
    finally:
        image.close()


def _old_jobs(plan, state):
    return OutboxMessage.objects.filter(
        event_type__in=JOB_EVENTS, payload__floorPlanId=str(plan.pk),
        payload__assetId=state["expected_asset_id"], payload__assetSha256=state["expected_sha256"],
        published_at__isnull=True, dead_lettered_at__isnull=True,
    )


def _cancel_jobs(plan, state):
    # The runtime queue helper also retires historical stale crop requests. It
    # assumes dedicated operations, so reject corrupt/shared scopes up front.
    for job in OutboxMessage.objects.select_for_update().filter(
        event_type="floor_plan.crop_evaluation.requested", payload__floorPlanId=str(plan.pk),
        published_at__isnull=True,
    ):
        operation_id = job.payload.get("operationId")
        if operation_id and OutboxMessage.objects.filter(payload__operationId=operation_id).exclude(
            payload__floorPlanId=str(plan.pk)
        ).exists():
            raise ValueError("Evaluation operation is shared; repair its scope before editing.")
    jobs = list(_old_jobs(plan, state).select_for_update())
    ids = [job.pk for job in jobs]
    for operation_id in {job.payload.get("operationId") for job in jobs} - {None, ""}:
        # Do not modify shared operations: fail closed before the queue helper,
        # which assumes one operation per layout job.
        if OutboxMessage.objects.filter(payload__operationId=operation_id).exclude(pk__in=ids).exists():
            raise ValueError("Evaluation operation is shared; repair its scope before editing.")
        Operation.objects.filter(pk=operation_id).exclude(state="completed").update(
            state="cancelled", error_code="STALE_LAYOUT_ASSET", updated_at=timezone.now(),
        )
    OutboxMessage.objects.filter(pk__in=ids).update(
        dead_lettered_at=timezone.now(), last_error_code="STALE_LAYOUT_ASSET",
    )
    return list(map(str, ids))


def _archive(plan):
    fields = {key: deepcopy(getattr(plan, key)) for key in FACT_DEFAULTS}
    raw = deepcopy(plan.raw_extraction)
    raw.pop("cropRevisionHistory", None)  # History is linear, not recursively copied.
    return json.loads(json.dumps({
        **fields, "raw_extraction": raw, "assetId": str(plan.asset_id),
        "assetSha256": plan.asset.sha256, "workflowRunId": str(plan.workflow_run_id),
        "plan_name": plan.plan_name, "display_name": plan.display_name,
    }, cls=DjangoJSONEncoder))


def _reconcile_reviews(plan, old_asset_id, new_asset_id):
    now = timezone.now()
    resolved = []
    for task in ReviewTask.objects.select_for_update().filter(
        task_type__in=["floor_plan", "layout_quality"], state="open",
        resolution__floorPlanId=str(plan.pk),
    ):
        reference = task.resolution.get("assetId") or task.resolution.get("sourceAssetId")
        if reference and reference != str(old_asset_id):
            continue
        if task.task_type == "layout_quality" and task.resolution.get("reasonCode") not in {
            "BAD_CROP", "MISSING_ROOMS", "WRONG_DIMENSIONS", "WRONG_FLAT_BOUNDARY",
        }:
            continue  # Duplicate/other reports are not fixed merely by recropping.
        task.state = "resolved"
        task.completed_at = now
        task.version += 1
        task.resolution = task.resolution | {
            "supersededByCropAssetId": str(new_asset_id), "resolutionCode": "IMAGE_REVISED",
            "previousAssetId": str(old_asset_id),
        }
        task.save(update_fields=["state", "completed_at", "version", "resolution", "updated_at"])
        resolved.append(str(task.pk))
    ReviewTask.objects.create(
        project=plan.project, task_type="floor_plan", priority=1,
        # Match the worker's exact get_or_create lookup to avoid a second task.
        resolution={"floorPlanId": str(plan.pk)},
    )
    return resolved


def update_layout_crop(plan_id, actor, *, editor_token, expected_asset_id, expected_sha256,
                       source_sha256, source_mode, regions, rotation_degrees=0, confirm=False):
    """Replace ONLY this plan's asset/facts, preserving identity and all old assets."""
    if connection.in_atomic_block:
        raise RuntimeError("Call crop update outside a database transaction.")
    if confirm is not True:
        raise ValueError("Confirm that this edit affects only the selected layout.")
    rectangle = validate_rectangle(regions)
    rotation = validate_rotation(rotation_degrees)
    state = _unsign(editor_token, plan_id, actor)
    for key, value in {"expected_asset_id": str(expected_asset_id), "expected_sha256": expected_sha256,
                       "source_sha256": source_sha256, "source_mode": source_mode}.items():
        if state.get(key) != value:
            raise StaleCropEdit("Submitted source does not match this crop editor. Reload it.")
    plan, destination = editable_layout(plan_id, actor, state["project_id"])
    require_enabled("media.crop_update", actor=actor, project=destination)
    require_enabled("media.extraction_queue", actor=actor, project=destination)
    with work_lock("document", plan.document_id), ExitStack() as locks:
        plan, destination = editable_layout(plan_id, actor, state["project_id"])
        data = _checked_preview(plan, destination, state)
        # Match worker document/outbox ownership. Nonblocking locks prevent a
        # cached old payload from being acknowledged across this edit.
        for job_id in _old_jobs(plan, state).order_by("pk").values_list("pk", flat=True):
            locks.enter_context(work_lock("outbox", job_id))
        crop, size, pixel_box = _render_crop(data, rectangle, rotation)
        if len(crop) > MAX_IMAGE_BYTES:
            raise ValueError("Cropped image exceeds the size limit.")
        plan, destination = editable_layout(plan_id, actor, state["project_id"])
        _check_version(plan, destination, state)
        new_asset_id = uuid4()
        key = storage.save(
            f"output/media/images/{destination.pk}/crop-revisions/{plan.pk}/{new_asset_id}.png",
            ContentFile(crop),
        )
        try:
            with transaction.atomic():
                list(Project.objects.select_for_update().filter(
                    pk__in=project_family_ids(destination)).order_by("pk"))
                FloorPlan.objects.select_for_update().get(pk=plan_id)
                EvidenceDocument.objects.select_for_update().get(pk=plan.document_id)
                FloorPlanAsset.objects.select_for_update().get(pk=plan.asset_id)
                if state["source_asset_id"]:
                    FloorPlanAsset.objects.select_for_update().get(pk=state["source_asset_id"])
                plan, destination = editable_layout(plan_id, actor, state["project_id"])
                _check_version(plan, destination, state)
                # Check the saved source metadata again without doing image I/O.
                source, page = _source(plan, destination, source_mode)
                if state["source_asset_id"]:
                    if not source or str(source.pk) != state["source_asset_id"] or source.sha256 != source_sha256:
                        raise StaleCropEdit("Original source changed. Reload the editor.")
                if page != state["page_number"]:
                    raise StaleCropEdit("Original source page changed. Reload the editor.")
                result = _persist(plan, destination, actor, state, rectangle, rotation,
                                  crop, size, pixel_box, new_asset_id, key)
            return result
        except Exception:
            # Only the uncommitted new object is disposable. Never delete old evidence.
            storage.delete(key)
            raise


def _persist(plan, destination, actor, state, rectangle, rotation, crop, size, pixel_box, asset_id, key,
             *, vision_profile_id=None):
    from .layout_reanalysis import queue_layout_evaluation  # Runtime integration; queue only.

    now = timezone.now()
    snapshot = _archive(plan)
    old_asset_id = plan.asset_id
    cancelled = _cancel_jobs(plan, state)
    bounds = {
        **rectangle, "unit": "normalized", "pixelBox": pixel_box,
        "sourceAssetId": state["source_asset_id"], "sourceDocumentId": state["document_id"],
        "sourceSha256": state["document_sha256"], "sourceImageSha256": state["source_sha256"],
        "pageNumber": state["page_number"], "sourceMode": state["source_mode"],
        "rotationDegreesClockwise": rotation, "segmentationMethod": "manual",
        "evidence": "reviewer_revised_boundary", "category": "floor-plans" if plan.plan_scope == "floor" else "layouts",
    }
    run = IngestionRun.objects.create(
        project=destination, document=plan.document, status="needs_review", pipeline_version=VERSION,
        extraction_provider="manual", started_at=now, completed_at=now,
        summary={"scope": "layout_crop_update", "floorPlanId": str(plan.pk)},
    )
    workflow = AgentWorkflowRun.objects.create(
        project=destination, document=plan.document, ingestion_run=run,
        workflow_type="layout_crop_update", workflow_version=VERSION, status="completed",
        started_at=now, completed_at=now, result={"floorPlanId": str(plan.pk), "previousAssetId": str(old_asset_id)},
    )
    asset = FloorPlanAsset.objects.create(
        id=asset_id, project=destination, document=plan.document, workflow_run=workflow,
        page_number=state["page_number"], asset_type="plan_crop" if plan.plan_scope == "floor" else "unit_crop", object_key=key,
        sha256=hashlib.sha256(crop).hexdigest(), byte_length=len(crop), pixel_width=size[0], pixel_height=size[1],
        source_bounds=bounds, detector_version=VERSION, detection_confidence=0,
    )
    raw = deepcopy(plan.raw_extraction)
    history = raw.get("cropRevisionHistory", [])
    if not isinstance(history, list):
        history = []
    revision = {
        "at": now.isoformat(), "actorId": str(actor.pk), "previous": snapshot,
        "assetId": str(asset.pk), "assetSha256": asset.sha256, "selection": bounds,
    }
    # Only provenance is carried forward. Unknown model fields live exclusively
    # in the archived snapshot and cannot accidentally masquerade as current facts.
    provenance_keys = (
        "draftId", "sourcePlanId", "sourcePlanName", "sourceAssetId", "sourceDocumentId",
        "segmentationMethod", "floors", "floorApplicability", "manualPageAddition",
    )
    current_raw = {key: raw[key] for key in provenance_keys if key in raw}
    if not current_raw.get("segmentationMethod"):
        current_raw["segmentationMethod"] = "manual"  # Evaluator must not rename the reviewer identity.
    current_raw.update({
        "boundingBox": bounds, "cropRevision": len(history) + 1,
        "cropRevisionHistory": [*history, revision], "extractionStale": True,
        "extractionAssetId": str(asset.pk), "extractionAssetSha256": asset.sha256,
    })
    if raw.get("qualityWarning"):
        current_raw["resolvedBoundaryWarning"] = {"assetId": str(old_asset_id), "reason": "image_revised"}
    plan.asset = asset
    plan.raw_extraction = current_raw
    plan.crop_remaining = False
    for field, default in FACT_DEFAULTS.items():
        setattr(plan, field, deepcopy(default))
    plan.save(update_fields=["asset", "raw_extraction", "crop_remaining", *FACT_DEFAULTS, "updated_at"])
    reconciled = _reconcile_reviews(plan, old_asset_id, asset.pk)
    if plan.plan_scope == "floor":
        from .floor_plan_layout_jobs import queue_floor_plan_layouts
        job = queue_floor_plan_layouts(plan, actor=actor, vision_profile_id=vision_profile_id)
        operation = Operation.objects.get(pk=job.payload["operationId"])
    else:
        job, operation = queue_layout_evaluation(plan, actor=actor, vision_profile_id=vision_profile_id)
    AuditEvent.objects.create(
        actor=actor, action="floor_plan.crop_updated", entity_type="FloorPlan", entity_id=plan.pk,
        correlation_id=job.correlation_id, reason_code="USER_REVISED_BOUNDARY",
        metadata={"projectId": str(destination.pk), "revision": revision, "cancelledJobIds": cancelled,
                  "reconciledReviewTaskIds": reconciled, "newJobId": str(job.pk), "onlyTargetLayout": True},
    )
    return {"floorPlanId": str(plan.pk), "assetId": str(asset.pk), "projectId": str(destination.pk),
            "messageId": str(job.pk), "operationId": str(operation.pk)}