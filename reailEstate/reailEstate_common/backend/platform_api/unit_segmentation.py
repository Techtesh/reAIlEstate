"""Persist individual dwellings from a full-floor image with local CV or AI boxes.

Pure geometry lives in domain.geometry and is re-exported here for compatibility.
Persistence depends on the leaf, never the reverse. Keep service-level bindings
(notably crop_region) so callers can still patch persistence collaborators here.
"""

import hashlib
import io
from uuid import uuid4

from PIL import Image
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from .artifact_storage import storage
from .feature_flags import FeatureDisabled, require_enabled
from .policies.crops import crop_destination
from .domain.geometry import (
    MAX_UNITS as MAX_UNITS,
    MIN_BOX_AREA as MIN_BOX_AREA,
    MAX_BOX_AREA as MAX_BOX_AREA,
    UnitRegion as UnitRegion,
    floor_applicability as floor_applicability,
    with_floor_applicability as with_floor_applicability,
    validate_regions as validate_regions,
    _content_mask as _content_mask,
    _red_markers as _red_markers,
    _dominant_markers as _dominant_markers,
    _regions_from_seeds as _regions_from_seeds,
    _radial_seeds as _radial_seeds,
    _colored_regions as _colored_regions,
    segment_units_script as segment_units_script,
    crop_region as crop_region,
)
from .models import AgentWorkflowRun, AuditEvent, FloorPlan, FloorPlanAsset, IngestionRun, OutboxMessage, ReviewTask

HIDDEN_SEGMENT_STATES = {"removed_by_user", "replaced_by_user", "superseded_page_candidate"}

def _hidden_segments_only(source_plan, workflows):
    """True when a prior run is retained solely as hidden review history."""
    plans = FloorPlan.objects.filter(workflow_run__in=workflows)
    return workflows.exists() and plans.exists() and not plans.exclude(review_state__in=HIDDEN_SEGMENT_STATES).exists()

def require_legacy_segmentation(source_plan, actor=None):
    """Keep the central exception/decision, but explain the immutable alternative."""
    try:
        require_enabled("operations.legacy_destructive", actor=actor, project=crop_destination(source_plan.project))
    except FeatureDisabled as exc:
        exc.args = ("FEATURE_DISABLED: operations.legacy_destructive. "
                    "Use the immutable Update this unit crop UI instead of replacing prior segmentation evidence.",)
        raise


def require_segment_replacement_allowed(source_plan, methods, actor=None, *, supersede_source=False):
    """Preflight before inference; a first generation is not destructive."""
    workflows = AgentWorkflowRun.objects.filter(
        workflow_type="unit_segmentation", result__sourceAssetId=str(source_plan.asset_id),
        result__segmentationMethod__in=methods,
    )
    if supersede_source or (workflows.exists() and not _hidden_segments_only(source_plan, workflows)):
        require_legacy_segmentation(source_plan, actor)

def replace_unit_segments(source_plan, regions, method, metadata=None, actor=None):
    """Replace one method's child crops atomically; preserve source and other methods."""
    if method not in {"script", "ai", "manual"}:
        raise ValueError("INVALID_SEGMENTATION_METHOD")
    if source_plan.review_state in {"removed_by_user", "replaced_by_user", "superseded_page_candidate"}:
        raise ValueError("FLOOR_PLAN_SOURCE_HIDDEN")
    source = source_plan.asset
    if source is None or source.status != "ready" or source.document.scan_status != "clean":
        raise ValueError("SOURCE_ASSET_NOT_READY")
    require_segment_replacement_allowed(source_plan, [method], actor)
    with storage.open(source.object_key, "rb") as stream:
        data = stream.read(source.byte_length + 1)
    if len(data) != source.byte_length or hashlib.sha256(data).hexdigest() != source.sha256:
        raise ValueError("IMAGE_INTEGRITY_MISMATCH")
    image = Image.open(io.BytesIO(data)).convert("RGB")
    rotation_degrees = int((metadata or {}).get("rotationDegreesCounterClockwise", 0))
    if rotation_degrees not in {-180, -90, 0, 90, 180}:
        raise ValueError("INVALID_LAYOUT_ROTATION")
    if rotation_degrees:
        image = image.rotate(
            rotation_degrees, expand=True, resample=Image.Resampling.BICUBIC, fillcolor="white",
        )
    keys = []
    try:
        with transaction.atomic():
            source_plan = FloorPlan.objects.select_for_update().get(id=source_plan.id)
            if source_plan.review_state in {"removed_by_user", "replaced_by_user", "superseded_page_candidate"}:
                raise ValueError("FLOOR_PLAN_SOURCE_HIDDEN")
            source = FloorPlanAsset.objects.select_for_update().get(id=source.id)
            old_workflows = AgentWorkflowRun.objects.filter(
                workflow_type="unit_segmentation", result__sourceAssetId=str(source.id),
                result__segmentationMethod=method,
            )
            # Repeat under the source locks: another generation may have committed
            # after preflight. Check before cancelling jobs or deleting any evidence.
            preserve_hidden_history = _hidden_segments_only(source_plan, old_workflows)
            if old_workflows.exists() and not preserve_hidden_history:
                require_legacy_segmentation(source_plan, actor)
            old_keys = []
            if not preserve_hidden_history:
                old_assets = FloorPlanAsset.objects.filter(workflow_run__in=old_workflows)
                old_keys = list(old_assets.values_list("object_key", flat=True))
                old_plan_ids = [str(value) for value in FloorPlan.objects.filter(workflow_run__in=old_workflows).values_list("id", flat=True)]
                OutboxMessage.objects.filter(
                    event_type="floor_plan.crop_evaluation.requested",
                    payload__floorPlanId__in=old_plan_ids,
                    published_at__isnull=True, dead_lettered_at__isnull=True,
                ).update(dead_lettered_at=timezone.now(), last_error_code="MANUAL_CROPS_REPLACED")
                ReviewTask.objects.filter(project=source.project, resolution__floorPlanId__in=old_plan_ids).delete()
                FloorPlan.objects.filter(workflow_run__in=old_workflows).delete()
                old_assets.delete()
                old_workflows.delete()
            run = IngestionRun.objects.create(
                project=source.project, document=source.document, pipeline_version=f"unit-segmentation-{method}-1.0",
                extraction_provider=method, status="completed", started_at=timezone.now(), completed_at=timezone.now(),
                summary={"sourcePlanId": str(source_plan.id), "sourceAssetId": str(source.id),
                         "segmentationMethod": method, "unitCount": len(regions), "metadata": metadata or {}},
            )
            workflow = AgentWorkflowRun.objects.create(
                project=source.project, document=source.document, ingestion_run=run,
                workflow_type="unit_segmentation", workflow_version=f"unit-segmentation-{method}-1.0",
                status="running", current_step="persist", started_at=timezone.now(),
            )
            plans = []
            # One microsecond timestamp distinguishes generations that retain history.
            name_timestamp = workflow.created_at.strftime("%Y%m%dT%H%M%S%fZ")
            for index, region in enumerate(regions, 1):
                crop, pixel_box, size = crop_region(image, region)
                digest = hashlib.sha256(crop).hexdigest()
                key = storage.save(
                    f"output/media/images/{source.project_id}/layouts/{source.id}/{method}/flat-{index:02d}-{uuid4().hex}.png",
                    ContentFile(crop),
                )
                keys.append(key)
                bounds = {"unit": "normalized", "x": round(region.x, 6), "y": round(region.y, 6),
                          "width": round(region.width, 6), "height": round(region.height, 6),
                          "pixelBox": list(pixel_box), "category": "layouts", "sourceAssetId": str(source.id),
                          "sourcePlanId": str(source_plan.id), "segmentationMethod": method,
                          "evidence": region.evidence, "floors": region.floors,
                          "rotationDegreesCounterClockwise": rotation_degrees}
                asset = FloorPlanAsset.objects.create(
                    project=source.project, document=source.document, workflow_run=workflow,
                    page_number=source.page_number, asset_type="unit_crop", object_key=key, byte_length=len(crop),
                    sha256=digest, pixel_width=size[0], pixel_height=size[1], source_bounds=bounds,
                    detector_version=f"unit-segmentation-{method}-1.0", detection_confidence=region.confidence,
                )
                plan = FloorPlan.objects.create(
                    project=source.project, document=source.document, workflow_run=workflow, asset=asset,
                    page_number=source.page_number, plan_scope="flat",
                    plan_name=f"{source_plan.id}_{index}_{name_timestamp}",
                    display_name=source_plan.display_title[:160],
                    orientation="", dimensions=[], features={"floors": region.floors},
                    raw_extraction={"sourcePlanId": str(source_plan.id), "sourceAssetId": str(source.id),
                                    "segmentationMethod": method, "boundingBox": bounds, "sourceLabel": region.label,
                                    "unitSegmentationPerformed": True, "metadata": metadata or {}},
                    confidence=region.confidence,
                    review_state={
                        "ai": "gen_with_ai_needs_review",
                        "manual": "manual_crop_needs_evaluation",
                    }.get(method, "script_segment_needs_review"),
                    extractor_version=f"unit-segmentation-{method}-1.0",
                )
                ReviewTask.objects.create(project=source.project, task_type="floor_plan", state="open", priority=2,
                                          resolution={"floorPlanId": str(plan.id)})
                plans.append(plan)
            crop_jobs = []
            if (metadata or {}).get("visionSelection"):
                from .layout_reanalysis import queue_layout_evaluation
                for plan in plans:
                    plan.review_state = "pending_crop_evaluation"
                    plan.save(update_fields=["review_state", "updated_at"])
                    message, _ = queue_layout_evaluation(plan, actor=actor, vision_selection=metadata["visionSelection"])
                    crop_jobs.append(str(message.pk))
            workflow.status = "completed"
            workflow.current_step = "completed"
            workflow.completed_at = timezone.now()
            workflow.confidence = sum(item.confidence for item in regions) / len(regions) if regions else 0
            workflow.result = {"sourcePlanId": str(source_plan.id), "sourceAssetId": str(source.id),
                               "segmentationMethod": method, "unitCount": len(plans),
                               "planIds": [str(item.id) for item in plans], "metadata": metadata or {},
                               "cropJobIds": crop_jobs}
            workflow.save()
            AuditEvent.objects.create(
                actor=actor, action="floor_plan.units_segmented", entity_type="FloorPlan", entity_id=source_plan.id,
                correlation_id=uuid4(), metadata={"method": method, "sourceAssetId": str(source.id),
                                                  "unitCount": len(plans)},
            )
            transaction.on_commit(lambda: [storage.delete(key) for key in old_keys], robust=True)
            return plans, workflow
    except Exception:
        for key in keys:
            storage.delete(key)
        raise


def supersede_full_floor_records(source_plan, methods, *, actor=None):
    """Retain one source/provenance record, remove duplicate pseudo-flat rows sharing its full-floor image."""
    require_legacy_segmentation(source_plan, actor)
    source = source_plan.asset
    if source is None:
        raise ValueError("SOURCE_ASSET_NOT_READY")
    siblings = FloorPlan.objects.filter(asset=source).exclude(id=source_plan.id)
    sibling_ids = [str(value) for value in siblings.values_list("id", flat=True)]
    with transaction.atomic():
        ReviewTask.objects.filter(project=source.project, resolution__floorPlanId__in=sibling_ids).delete()
        removed = siblings.count()
        siblings.delete()
        source_plan.plan_scope = "floor"
        source_plan.review_state = "superseded_by_unit_segments"
        source_plan.raw_extraction = source_plan.raw_extraction | {
            "unitSegmentationMethods": methods, "unitSegmentationPerformed": True,
            "supersededAt": timezone.now().isoformat(), "removedDuplicateFullImagePlans": removed,
        }
        source_plan.save(update_fields=["plan_scope", "review_state", "raw_extraction", "updated_at"])
        ReviewTask.objects.filter(project=source.project, resolution__floorPlanId=str(source_plan.id)).update(
            state="resolved", completed_at=timezone.now())
    return removed
