"""Shared status-aware document reanalysis queueing."""

from uuid import uuid4

from django.db import transaction

from .floor_plan_vision import resolve_vision_selection
from .models import AuditEvent, EvidenceDocument, ExtractionJob, Operation, OutboxMessage
from .workers import enqueue_document


RETRYABLE_DOCUMENT_STATUSES = {"quarantined", "extraction_failed", "needs_review", "approved"}


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
