"""Shared orchestration for ad hoc script and AI flat separation."""

from .artifact_storage import storage
from .feature_flags import FeatureDisabled, require_enabled
from .floor_plan_ai_skill import FloorPlanFlatSeparationAISkill
from .infrastructure.verified_assets import read_verified_asset
from .models import FloorPlan, FloorPlanAsset
from .policies.crops import authorize_crops, crop_destination
from .unit_segmentation import (
    floor_applicability,
    replace_unit_segments,
    require_segment_replacement_allowed,
    segment_units_script,
    supersede_full_floor_records,
    with_floor_applicability,
)


MODES = {"script", "ai", "both"}


class FlatSeparationFailed(Exception):
    def __init__(self, result):
        super().__init__("FLAT_SEPARATION_FAILED")
        self.result = result


def available_flat_separation_ai_profiles():
    return FloorPlanFlatSeparationAISkill.profiles()


def _load_source(floor_plan_id):
    plan = FloorPlan.objects.select_related(
        "asset", "project", "document", "workflow_run__ingestion_run"
    ).get(id=floor_plan_id)
    if plan.asset is None or plan.asset.asset_type == "unit_crop":
        raise ValueError("FULL_FLOOR_SOURCE_REQUIRED")
    if plan.asset.status != "ready" or plan.document.scan_status != "clean":
        raise ValueError("SOURCE_ASSET_NOT_READY")
    data = read_verified_asset(plan.asset, storage_backend=storage)
    return plan, data


def _page_text(plan):
    text = " ".join(plan.workflow_run.ingestion_run.chunks.filter(
        page_number=plan.page_number
    ).values_list("content", flat=True))
    if text:
        return text
    original_id = plan.asset.source_bounds.get("sourceAssetId")
    original = FloorPlanAsset.objects.filter(id=original_id).select_related(
        "workflow_run__ingestion_run"
    ).first()
    if original:
        return " ".join(original.workflow_run.ingestion_run.chunks.filter(
            page_number=plan.page_number
        ).values_list("content", flat=True))
    return ""


def separate_floor_plan_units(floor_plan_id, mode="script", expected_count=None,
                              vision_profile_id=None, supersede_source=False, actor=None):
    if mode not in MODES:
        raise ValueError("INVALID_SEGMENTATION_MODE")
    if expected_count is not None and (
        isinstance(expected_count, bool) or not isinstance(expected_count, int) or not 1 <= expected_count <= 40
    ):
        raise ValueError("INVALID_EXPECTED_COUNT")
    if mode == "script" and vision_profile_id is not None:
        raise ValueError("VISION_PROFILE_NOT_USED")

    plan, data = _load_source(floor_plan_id)
    destination = crop_destination(plan.project)
    if actor is not None:
        authorize_crops(actor, destination)
    if plan.review_state in {"removed_by_user", "replaced_by_user", "superseded_page_candidate"}:
        raise ValueError("FLOOR_PLAN_SOURCE_HIDDEN")
    require_enabled("media.auto_segment", actor=actor, project=destination)
    floor_labels = floor_applicability(_page_text(plan))
    methods = ["script", "ai"] if mode == "both" else [mode]
    require_segment_replacement_allowed(plan, methods, actor, supersede_source=supersede_source)
    result = {
        "sourcePlanId": str(plan.id), "sourceAssetId": str(plan.asset_id),
        "projectId": str(plan.project_id), "mode": mode, "expectedCount": expected_count,
        "methods": {}, "sourceSuperseded": False,
    }
    completed = []
    for method in methods:
        try:
            if method == "script":
                regions, metadata = segment_units_script(data, expected_count)
            else:
                regions, metadata = FloorPlanFlatSeparationAISkill.invoke(
                    data, plan.asset.media_type, expected_count, vision_profile_id,
                )
            regions = with_floor_applicability(regions, floor_labels)
            plans, workflow = replace_unit_segments(plan, regions, method, metadata, actor=actor)
            result["methods"][method] = {
                "status": "completed", "unitCount": len(plans), "workflowRunId": str(workflow.id),
                "planIds": [str(item.id) for item in plans],
                "assets": [{"assetId": str(item.asset_id),
                            "uri": f"/api/v1/media/floor-plan-assets/{item.asset_id}"} for item in plans],
                "metadata": metadata,
            }
            completed.append(method)
        except FeatureDisabled:
            # Preserve the central decision/key/code, including a persistence race.
            raise
        except Exception as exc:
            error_code = str(exc) if isinstance(exc, ValueError) else getattr(exc, "code", type(exc).__name__)
            result["methods"][method] = {"status": "failed", "errorCode": str(error_code)[:100]}
            if mode != "both":
                raise FlatSeparationFailed(result) from exc
    result["status"] = "completed" if len(completed) == len(methods) else "partially_completed"
    if not completed:
        raise FlatSeparationFailed(result)
    if supersede_source and len(completed) == len(methods):
        result["removedDuplicateFullImagePlans"] = supersede_full_floor_records(plan, methods, actor=actor)
        result["sourceSuperseded"] = True
    elif supersede_source:
        result["supersedeSkipped"] = "NOT_ALL_REQUESTED_METHODS_COMPLETED"
    return result