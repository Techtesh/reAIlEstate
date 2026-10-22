"""Shared status-aware document reanalysis queueing."""

from uuid import uuid4

from django.core import signing
from django.db import models, transaction

from .floor_plan_vision import resolve_vision_selection
from .models import AuditEvent, EvidenceDocument, ExtractionJob, FloorPlan, FloorPlanAsset, Operation, OutboxMessage
from .workers import enqueue_document


RETRYABLE_DOCUMENT_STATUSES = {"quarantined", "extraction_failed", "needs_review", "approved"}
RETRY_WARNING_ACTIONS = {
    "queue_project_reanalysis", "queue_failed_reanalysis", "run_project_evaluations",
    "run_layout_evaluation", "queue_floor_layouts", "queue_amenity_pages",
}


def fresh_evaluation_plans(project):
    """Current failed layout/floor attempts only, including deleted DLQ entries."""
    from .floor_plan_visibility import visible_floor_plans
    plans = {str(plan.pk): plan for plan in visible_floor_plans(
        FloorPlan.objects.filter(project=project, plan_scope__in=["flat", "floor"])).select_related("asset")}
    operations = Operation.objects.filter(
        models.Q(result__floorPlanId__in=list(plans)) | models.Q(result__sourcePlanId__in=list(plans)),
        kind__in=["floor_plan_crop_evaluation", "floor_plan_units_evaluation"],
    ).order_by("-created_at", "-pk")
    seen, failed = set(), []
    for operation in operations:
        key = operation.result.get("floorPlanId") or operation.result.get("sourcePlanId")
        if key in seen:
            continue
        seen.add(key)
        plan = plans.get(key)
        expected_kind = "floor_plan_units_evaluation" if plan and plan.plan_scope == "floor" else "floor_plan_crop_evaluation"
        if (plan and operation.state == "failed" and operation.kind == expected_kind and plan.asset_id
                and operation.result.get("assetId") == str(plan.asset_id)
                and operation.result.get("assetSha256") == plan.asset.sha256):
            failed.append(plan)
    return failed


def queue_fresh_project_evaluations(project, actor):
    from .floor_plan_layout_jobs import queue_floor_plan_layouts
    from .layout_evaluation_status import queue_single_layout
    queued = []
    for plan in fresh_evaluation_plans(project):
        if plan.plan_scope == "floor":
            job = queue_floor_plan_layouts(plan, actor, fresh=True)
            queued.append(str(job.pk))
        else:
            queued.append(queue_single_layout(project, plan.pk, actor, fresh=True)["messageId"])
    return queued


def evaluation_retry_warning(project, actor, submitted):
    import hashlib
    import json
    from .jobs.events import EXTRACTION_EVENTS
    from .project_lifecycle import project_family_ids

    action = submitted.get("action")
    if action not in RETRY_WARNING_ACTIONS:
        return None
    family = project_family_ids(project)
    documents = EvidenceDocument.objects.filter(project_id__in=family).order_by("pk")
    plans = FloorPlan.objects.filter(project_id__in=family).order_by("pk")
    if action == "run_project_evaluations":
        documents = documents.filter(project=project)
        plans = plans.filter(project=project)
    if action == "queue_failed_reanalysis":
        documents = documents.filter(status="extraction_failed")
    if action in {"run_layout_evaluation", "queue_floor_layouts"}:
        from django.core.exceptions import ValidationError
        from django.http import Http404
        from django.shortcuts import get_object_or_404
        try:
            target = get_object_or_404(plans, pk=submitted.get("floor_plan_id"))
        except ValidationError:
            raise Http404 from None
        plans = plans.filter(pk=target.pk)
    document_rows = {str(document.pk): document for document in documents}
    plan_rows = {str(plan.pk): plan for plan in plans}
    scope = models.Q(payload__documentId__in=list(document_rows)) | models.Q(payload__floorPlanId__in=list(plan_rows))
    operation_scope = models.Q(result__documentId__in=list(document_rows)) | models.Q(result__floorPlanId__in=list(plan_rows))
    if action in {"queue_project_reanalysis", "queue_failed_reanalysis"}:
        event_types = ["document.processing.requested"]
        scope = models.Q(payload__documentId__in=list(document_rows))
        operation_scope = models.Q(kind="document_processing", result__documentId__in=list(document_rows))
    elif action in {"run_layout_evaluation", "queue_floor_layouts"}:
        event_types = ["floor_plan.units_evaluation.requested"] if action == "queue_floor_layouts" else [
            "floor_plan.crop_evaluation.requested", "source.page_evaluation.requested"]
        scope = models.Q(payload__floorPlanId__in=list(plan_rows)) | models.Q(payload__sourcePlanId__in=list(plan_rows))
        operation_scope = models.Q(result__floorPlanId__in=list(plan_rows)) | models.Q(result__sourcePlanId__in=list(plan_rows))
    elif action == "queue_amenity_pages":
        assets = [str(key) for key in FloorPlanAsset.objects.filter(project_id__in=family, status="ready",
            source_bounds__manualPageAddition=True, source_bounds__category="amenities").values_list("pk", flat=True)]
        event_types = ["source.page_evaluation.requested"]
        scope = models.Q(payload__assetId__in=assets)
        operation_scope = models.Q(result__assetId__in=assets)
    else:
        event_types = EXTRACTION_EVENTS
        scope |= models.Q(payload__projectId=str(project.pk))
        operation_scope |= models.Q(result__projectId=str(project.pk))
    jobs = list(OutboxMessage.objects.filter(scope, event_type__in=event_types).order_by("pk"))
    operations = {str(operation.pk): operation for operation in Operation.objects.filter(
        models.Q(pk__in=[job.payload["operationId"] for job in jobs if job.payload.get("operationId")])
        | (operation_scope & models.Q(state="failed")))}
    failures = []
    seen_operations = set()

    def label(payload):
        plan = plan_rows.get(payload.get("floorPlanId") or payload.get("sourcePlanId"))
        document = document_rows.get(payload.get("documentId"))
        return plan.display_title if plan else document.original_name if document else project.submitted_name

    for job in jobs:
        operation = operations.get(job.payload.get("operationId"))
        if operation:
            seen_operations.add(str(operation.pk))
        if operation and operation.state == "cancelled":
            continue
        if not job.last_error_code and not job.dead_lettered_at and not (operation and operation.state == "failed"):
            continue
        failures.append({"id": str(job.pk), "label": label(job.payload), "attempts": job.attempts,
            "error": job.last_error_code or (operation.error_code if operation else "") or "Previous evaluation failed",
            "version": job.dead_lettered_at.isoformat() if job.dead_lettered_at else str(job.attempts)})
    for key, operation in sorted(operations.items()):
        if key not in seen_operations and operation.state == "failed":
            failures.append({"id": key, "label": label(operation.result), "attempts": None,
                "error": operation.error_code or "Previous evaluation failed", "version": operation.updated_at.isoformat()})
    if action in {"queue_project_reanalysis", "queue_failed_reanalysis", "run_project_evaluations"}:
        for document in document_rows.values():
            if document.error_category:
                failures.append({"id": str(document.pk), "label": document.original_name, "attempts": None,
                    "error": document.error_category, "version": document.updated_at.isoformat()})
    if not failures:
        return None
    data = {key: submitted.getlist(key) for key in sorted(submitted)
            if key not in {"csrfmiddlewaretoken", "retry_confirmation"}}
    fingerprint = hashlib.sha256(json.dumps(failures, sort_keys=True).encode()).hexdigest()
    confirmation = {"actor": str(actor.pk), "project": str(project.pk), "request": data, "failures": fingerprint}
    token = submitted.get("retry_confirmation", "")
    if token:
        try:
            if signing.loads(token, salt="evaluation-retry", max_age=300) == confirmation:
                return None
        except signing.BadSignature:
            pass
    warning = {"project": project.submitted_name, "failures": failures[:20], "total": len(failures),
               "confirmationToken": signing.dumps(confirmation, salt="evaluation-retry", compress=True)}
    if (action in {"run_layout_evaluation", "queue_floor_layouts"}
            or (action == "run_project_evaluations" and fresh_evaluation_plans(project))):
        fresh_confirmation = {**confirmation, "request": {**data, "fresh_evaluation": ["true"]}}
        warning["freshConfirmationToken"] = signing.dumps(fresh_confirmation, salt="evaluation-retry", compress=True)
    return warning


@transaction.atomic
def queue_document_reanalysis(document_id, actor, reason_code="ADMIN_REANALYSIS", vision_profile_id=None, allow_merged=False):
    document = EvidenceDocument.objects.select_for_update().select_related("project").get(id=document_id)
    if document.project.status == "retired" or (document.project.status == "merged" and not allow_merged):
        raise ValueError("PROJECT_NOT_ACTIVE")
    if document.status not in RETRYABLE_DOCUMENT_STATUSES:
        raise ValueError("DOCUMENT_NOT_REPROCESSABLE")
    if OutboxMessage.objects.filter(
        event_type="document.processing.requested", payload__documentId=str(document.id),
        published_at__isnull=True, dead_lettered_at__isnull=True,
    ).exists():
        return None
    selection = resolve_vision_selection(vision_profile_id)
    document.status = "quarantined" if document.scan_status != "clean" else "extraction_failed"
    document.error_category = ""
    document.save(update_fields=["status", "error_category", "updated_at"])
    ExtractionJob.objects.create(document=document, extractor_version="extractor-1.0", status="queued")
    correlation_id = uuid4()
    message = enqueue_document(document, correlation_id, vision_selection=selection)
    operation = Operation.objects.create(
        owner=actor, kind="document_processing", state="pending", result={"documentId": str(document.id)},
    )
    message.payload = message.payload | {"operationId": str(operation.pk)}
    message.save(update_fields=["payload"])
    AuditEvent.objects.create(
        actor=actor, action="document.reprocessing_requested", entity_type="EvidenceDocument",
        entity_id=document.id, reason_code=reason_code[:64], correlation_id=correlation_id,
        metadata={"operationId": str(operation.id)},
    )
    return message, operation


def queue_project_reanalysis(project, actor, failed_only=False, vision_profile_id=None):
    from .project_lifecycle import project_family_ids
    documents = EvidenceDocument.objects.filter(project_id__in=project_family_ids(project)).order_by("created_at")
    if failed_only:
        documents = documents.filter(status="extraction_failed")
    queued = []
    for document_id in documents.values_list("id", flat=True):
        try:
            result = queue_document_reanalysis(
                document_id, actor, "PROJECT_REANALYSIS", vision_profile_id, allow_merged=True,
            )
        except ValueError:
            continue
        if result:
            message, operation = result
            queued.append({"documentId": str(document_id), "messageId": str(message.id), "operationId": str(operation.id)})
    return queued
