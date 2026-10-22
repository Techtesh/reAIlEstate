"""Reusable selection and queueing for saved-layout evaluation batches."""

from dataclasses import dataclass
from enum import StrEnum

from .floor_plan_visibility import HIDDEN_REVIEW_STATES
from .layout_reanalysis import layout_evaluation_error, queue_layout_evaluation
from .models import FloorPlan


class EvaluationMode(StrEnum):
    NEW = "new"
    RETRY_FAILED = "retry-failed"
    FORCE = "force"


@dataclass(frozen=True)
class EvaluationOutcome:
    plan_id: str
    status: str
    error: str = ""


def eligible_layouts(*, project_id=None, limit=None):
    if limit is not None and limit < 1:
        raise ValueError("--limit must be at least 1.")
    layouts = FloorPlan.objects.filter(
        plan_scope="flat", crop_remaining=False, asset__status="ready", document__scan_status="clean",
    ).exclude(review_state__in=HIDDEN_REVIEW_STATES).exclude(
        project__status__in={"retired", "merged"},
    ).select_related("project", "document", "asset").order_by("project_id", "id")
    if project_id is not None:
        layouts = layouts.filter(project_id=project_id)
    return layouts[:limit] if limit is not None else layouts


def queue_layout_batch(layouts, *, vision_selection, mode=EvaluationMode.NEW, actor=None):
    mode = EvaluationMode(mode)
    for plan in layouts:
        try:
            message, operation = queue_layout_evaluation(
                plan, actor=actor, vision_selection=vision_selection,
                retry=mode in {EvaluationMode.RETRY_FAILED, EvaluationMode.FORCE},
                force=mode == EvaluationMode.FORCE,
            )
        except (FloorPlan.DoesNotExist, ValueError) as exc:
            yield EvaluationOutcome(str(plan.pk), "skipped", layout_evaluation_error(exc))
            continue
        status = "queued" if (operation.state == "pending" and message.published_at is None
                              and message.dead_lettered_at is None) else "reused"
        yield EvaluationOutcome(str(plan.pk), status)