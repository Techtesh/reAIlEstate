"""Explicit, audited, reversible record removal; never delete source evidence."""

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from itertools import islice
from uuid import UUID, uuid4

from django.core.exceptions import PermissionDenied
from django.db import OperationalError, connection, transaction
from django.utils import timezone

from .floor_plan_visibility import HIDDEN_REVIEW_STATES, REMOVED_REVIEW_STATE
from .ingestion_runtime import WorkAlreadyClaimed, work_lock
from .models import AuditEvent, EvidenceDocument, FloorPlan, Operation, OutboxMessage
from .project_lifecycle import project_family_ids

PLAN_JOB_EVENTS = ("floor_plan.crop_evaluation.requested", "source.page_evaluation.requested", "floor_plan.units_evaluation.requested")
REMOVAL_ERROR = "FLOOR_PLAN_REMOVED_BY_USER"
MAX_BULK_LAYOUTS = 200


@dataclass(frozen=True)
class BulkRemovalResult:
    selected_count: int
    removed_count: int
    unchanged_count: int
    cancelled_job_count: int


def normalize_plan_ids(plan_ids):
    """Bound submitted selections before querying; deduplicate canonical UUIDs."""
    if isinstance(plan_ids, (str, bytes)):
        raise ValueError("Submit a list of layout IDs.")
    values = list(islice(plan_ids, MAX_BULK_LAYOUTS + 1))
    if not values or len(values) > MAX_BULK_LAYOUTS:
        raise ValueError(f"Select between 1 and {MAX_BULK_LAYOUTS} layouts.")
    try:
        return sorted({UUID(str(value)) for value in values}, key=str)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Select valid layout IDs.") from exc


def authorize_plan_removal(actor, project):
    from .builder_access import builder_can_manage
    if builder_can_manage(actor, project) and project.status not in {"retired", "merged"}:
        return
    # Staff have global project access in this application's authorization model.
    # Still require project access AND the explicit model change permission.
    if not actor or not actor.is_active or not actor.is_staff or not actor.has_perm(
        "platform_api.change_floorplan"
    ) or not (actor.has_perm("platform_api.view_project") or actor.has_perm("platform_api.change_project")):
        raise PermissionDenied


def _pending_jobs(plan_id):
    # Never match on asset, source page, workflow, or document: those may be shared.
    return OutboxMessage.objects.filter(
        event_type__in=PLAN_JOB_EVENTS, payload__floorPlanId=str(plan_id),
        published_at__isnull=True, dead_lettered_at__isnull=True,
    )


def _pending_batch_jobs(plan_ids):
    return OutboxMessage.objects.filter(
        event_type__in=PLAN_JOB_EVENTS, payload__floorPlanId__in=[str(pk) for pk in plan_ids],
        published_at__isnull=True, dead_lettered_at__isnull=True,
    )


def _row_locks(queryset):
    # Fail fast on PostgreSQL contention. SQLite uses the document/outbox file
    # locks plus its write transaction; a concurrent writer may raise busy.
    return queryset.select_for_update(nowait=connection.features.has_select_for_update_nowait)


@contextmanager
def _removal_transaction():
    # Translate lock/busy errors only after the transaction has rolled back;
    # single-record callers already handle WorkAlreadyClaimed, not DB errors.
    try:
        with transaction.atomic():
            yield
    except OperationalError as exc:
        raise WorkAlreadyClaimed("Layout records or jobs are busy. Retry the entire selection.") from exc


@contextmanager
def _locked_removal_plans(project, plan_ids, actor):
    """Acquire each work lock once, outside the short, shared DB transaction.

    Queue creation/retry need not take a document advisory lock. Document then
    plan row locks serialize with those writers; re-reading pending jobs under
    these locks catches a job inserted since advisory-lock discovery. Never
    acquire a newly discovered work lock inside the transaction: fail closed.
    """
    authorize_plan_removal(actor, project)
    snapshots = dict(FloorPlan.objects.filter(
        pk__in=plan_ids, project_id__in=project_family_ids(project)
    ).values_list("pk", "document_id"))
    if len(snapshots) != len(plan_ids):
        raise FloorPlan.DoesNotExist("One or more selected layouts are unavailable in this project.")
    with ExitStack() as locks:
        document_ids = sorted(set(snapshots.values()), key=str)
        for document_id in document_ids:
            locks.enter_context(work_lock("document", document_id))
        job_ids = list(_pending_batch_jobs(plan_ids).order_by("pk").values_list("pk", flat=True))
        for job_id in job_ids:
            locks.enter_context(work_lock("outbox", job_id))
        with _removal_transaction():
            list(_row_locks(EvidenceDocument.objects.filter(pk__in=document_ids)).order_by("pk"))
            plans = list(_row_locks(FloorPlan.objects.filter(
                pk__in=plan_ids, project_id__in=project_family_ids(project)
            )).order_by("pk"))
            authorize_plan_removal(actor, project)
            if len(plans) != len(plan_ids):
                raise FloorPlan.DoesNotExist("One or more selected layouts are unavailable in this project.")
            if any(plan.document_id != snapshots[plan.pk] for plan in plans):
                raise ValueError("This record changed. Reload the confirmation page before continuing.")
            jobs = list(_row_locks(_pending_batch_jobs(plan_ids)).order_by("pk"))
            if {job.pk for job in jobs} - set(job_ids):
                raise WorkAlreadyClaimed("Layout jobs changed. Reload the confirmation page and retry.")
            operation_ids = {job.payload.get("operationId") for job in jobs} - {None, ""}
            list(_row_locks(Operation.objects.filter(pk__in=operation_ids)).order_by("pk"))
            by_plan = {str(pk): [] for pk in plan_ids}
            for job in jobs:
                by_plan[job.payload["floorPlanId"]].append(job)
            yield plans, by_plan


def _set_floor_plan_removed_locked(project, plan, actor, *, removed, jobs):
    """Only called with plan/job row locks and the shared transaction held."""
    if (plan.review_state == REMOVED_REVIEW_STATE) == removed:
        return plan, False
    metadata = {"projectId": str(plan.project_id), "dashboardProjectId": str(project.pk),
                "previousReviewState": plan.review_state, "assetId": str(plan.asset_id) if plan.asset_id else None}
    if removed:
        job_ids = [job.pk for job in jobs]
        operation_ids = {job.payload.get("operationId") for job in jobs} - {None, ""}
        cancelled_operations = []
        for operation_id in sorted(operation_ids):
            # Never cancel a shared operation, even when the other plan happens
            # to be in this batch. The per-record audit stays restore-compatible.
            if OutboxMessage.objects.filter(payload__operationId=operation_id).exclude(pk__in=job_ids).exists():
                continue
            if Operation.objects.filter(pk=operation_id, state__in=["pending", "queued", "running", "failed"]).update(
                state="cancelled", error_code=REMOVAL_ERROR, updated_at=timezone.now()
            ):
                cancelled_operations.append(operation_id)
        OutboxMessage.objects.filter(pk__in=job_ids).update(
            dead_lettered_at=timezone.now(), last_error_code=REMOVAL_ERROR
        )
        metadata.update(cancelledJobIds=list(map(str, job_ids)), cancelledOperationIds=cancelled_operations)
        plan.review_state = REMOVED_REVIEW_STATE
    else:
        removal = AuditEvent.objects.filter(
            entity_type="FloorPlan", entity_id=plan.pk, action="floor_plan.removed_by_user"
        ).order_by("-occurred_at", "-pk").first()
        prior_state = removal.metadata.get("previousReviewState") if removal else None
        if not prior_state or prior_state == REMOVED_REVIEW_STATE:
            raise ValueError("No valid removal audit is available; this record cannot be restored automatically.")
        plan.review_state = prior_state
        metadata.update(removalAuditId=str(removal.pk), restoredReviewState=prior_state, jobsRestarted=False)
    plan.save(update_fields=["review_state", "updated_at"])
    AuditEvent.objects.create(
        actor=actor, action="floor_plan.removed_by_user" if removed else "floor_plan.restored_by_user",
        entity_type="FloorPlan", entity_id=plan.pk, correlation_id=uuid4(),
        reason_code="USER_SELECTED_DUPLICATE" if removed else "USER_RESTORE", metadata=metadata,
    )
    return plan, True


def set_floor_plan_removed(project, plan_id, actor, *, removed, expected_updated_at=None):
    """Return (plan, changed). Restore visibility only; never restart jobs.

    Preserve the original optional timestamp and idempotent single-record API.
    """
    with _locked_removal_plans(project, [UUID(str(plan_id))], actor) as (plans, jobs):
        plan = plans[0]
        if (plan.review_state == REMOVED_REVIEW_STATE) == removed:
            return plan, False
        if expected_updated_at is not None and plan.updated_at != expected_updated_at:
            raise ValueError("This record changed. Reload the confirmation page before continuing.")
        return _set_floor_plan_removed_locked(project, plan, actor, removed=removed, jobs=jobs[str(plan.pk)])


def remove_floor_plans(project, plan_ids, actor, *, expected_updated_at):
    """Atomically remove 1–200 visible flats; require every confirmed version.

    expected_updated_at maps UUIDs (or their strings) to datetime values. No
    evidence, assets, child records or unrelated jobs are deleted or changed.
    A caller must retry the entire batch after a stale, unauthorized or busy error.
    """
    authorize_plan_removal(actor, project)
    ids = normalize_plan_ids(plan_ids)
    if not isinstance(expected_updated_at, dict):
        raise ValueError("Every selected layout requires a confirmation timestamp.")
    versions = {str(key): value for key, value in expected_updated_at.items()}
    if set(versions) != {str(pk) for pk in ids} or any(value is None for value in versions.values()):
        raise ValueError("Every selected layout requires a confirmation timestamp.")
    with _locked_removal_plans(project, ids, actor) as (plans, jobs):
        # Preflight the whole locked set before the first cancellation or audit.
        for plan in plans:
            if plan.updated_at != versions[str(plan.pk)]:
                raise ValueError("A selected layout changed. Reload the confirmation page before continuing.")
            if plan.plan_scope != "flat" or plan.review_state in HIDDEN_REVIEW_STATES:
                raise ValueError("Select only visible individual layouts. Reload the project layouts.")
        changed = sum(_set_floor_plan_removed_locked(
            project, plan, actor, removed=True, jobs=jobs[str(plan.pk)]
        )[1] for plan in plans)
        return BulkRemovalResult(len(plans), changed, len(plans) - changed,
                                 sum(len(plan_jobs) for plan_jobs in jobs.values()))