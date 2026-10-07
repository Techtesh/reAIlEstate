from contextlib import ExitStack
from datetime import timedelta
import hashlib
import re

from .artifact_storage import storage as default_storage, move_document
from django.db import connection, models, transaction
from django.db.models import Q
from django.utils import timezone

from .business_ingestion import extract_project_knowledge
from .builder_scan_policy import scan_builder_document
from .floor_plan_vision import get_floor_plan_vision_provider, resolve_vision_selection
from .integrations import ProviderUnavailable, get_document_scanner
from .models import EvidenceDocument, InboxMessage, Operation, OutboxMessage, RecommendationSnapshot, RequirementVersion, ReviewTask
from .rag import ingest_document, DocumentTextExtractor
from .document_formats import office_signature, CONVERTED_FORMATS, prepare_document
from .document_images import collect_document_images
from .project_outputs import build_project_output, materialize_project_amenities, store_outputs
from .project_vision import visual_project_claims
from .services import compute_recommendations
from .ingestion_runtime import DocumentProgress, event, work_lock, WorkAlreadyClaimed


_MEDIA_SIGNATURES = {
    b"%PDF-": "application/pdf",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
}


def detect_media_type(file_object, declared_media_type=None):
    prefix = file_object.read(16)
    file_object.seek(0)
    for signature, media_type in _MEDIA_SIGNATURES.items():
        if prefix.startswith(signature):
            return media_type
    if declared_media_type in {"text/plain", "text/markdown", "text/csv", "application/json"} and b"\x00" not in prefix:
        try:
            prefix.decode("utf-8")
            return declared_media_type
        except UnicodeDecodeError:
            pass
    if declared_media_type in CONVERTED_FORMATS and office_signature(file_object, declared_media_type):
        return declared_media_type
    return "application/octet-stream"


def process_document(document_id, scanner=None, extractor=None, vision_selection=None, progress_context=None):
    # Never hold a row/database transaction while scanning, rendering or calling providers.
    with work_lock("document", document_id), DocumentProgress(document_id, progress_context):
        result = _process_document(document_id, scanner, extractor, vision_selection)
        run = result.ingestion_runs.order_by("-created_at").first()
        stage = ("awaiting_visual_analysis" if run and run.status == "needs_provider" else "completed") if result.status in {
            "needs_review", "approved"} else result.status
        event(stage, documentStatus=result.status, errorCode=result.error_category)
        return result


def _process_document(document_id, scanner=None, extractor=None, vision_selection=None):
    scanner = scanner or get_document_scanner()
    document = EvidenceDocument.objects.get(id=document_id)
    job = document.extraction_jobs.order_by("-created_at").first()
    operation = Operation.objects.filter(kind="document_processing", result__documentId=str(document.id)).order_by("-created_at").first()
    if document.status not in ["quarantined", "extraction_failed", "extracting"]:
        return document
    # An abandoned 'extracting' document is safe to retry only after acquiring its ownership lock.
    document.ingestion_runs.filter(status="extracting").update(status="failed", error_code="WORKER_INTERRUPTED")
    event("source_validation", originalName=document.original_name)
    try:
        with default_storage.open(document.object_key, "rb") as content:
            original = content.read(document.byte_length + 1)
            if len(original) != document.byte_length or hashlib.sha256(original).hexdigest() != document.sha256:
                document.status = "rejected"
                document.scan_status = "rejected"
                document.error_category = "SOURCE_INTEGRITY_MISMATCH"
                document.save(update_fields=["status", "scan_status", "error_category", "updated_at"])
                if job:
                    job.status = "rejected"
                    job.error_category = document.error_category
                    job.completed_at = timezone.now()
                    job.save()
                if operation:
                    operation.state = "failed"
                    operation.error_code = document.error_category
                    operation.save()
                return document
            del original
            content.seek(0)
            detected = detect_media_type(content, document.declared_media_type)
            if detected != document.declared_media_type:
                document.detected_media_type = detected
                document.scan_status = "rejected"
                document.status = "rejected"
                document.error_category = "MEDIA_TYPE_MISMATCH"
                document.save()
                if operation:
                    operation.state = "failed"
                    operation.error_code = "MEDIA_TYPE_MISMATCH"
                    operation.save(update_fields=["state", "error_code", "updated_at"])
                return document
            scan_result = scan_builder_document(document, content, scanner)
            if scan_result != "clean":
                document.scan_status = "rejected"
                document.status = "rejected"
                document.error_category = "MALWARE_DETECTED"
                document.save()
                if job:
                    job.status = "rejected"
                    job.error_category = "MALWARE_DETECTED"
                    job.completed_at = timezone.now()
                    job.save(update_fields=["status", "error_category", "completed_at", "updated_at"])
                if operation:
                    operation.state = "failed"
                    operation.error_code = "MALWARE_DETECTED"
                    operation.save(update_fields=["state", "error_code", "updated_at"])
                return document
            document.scan_status = "clean"
            document.detected_media_type = detected
            document.status = "extracting"
            document.save(update_fields=["scan_status", "detected_media_type", "status", "updated_at"])
            if job:
                job.status = "extracting"
                job.attempt += 1
                job.started_at = timezone.now()
                job.error_category = ""
                job.save(update_fields=["status", "attempt", "started_at", "error_category", "updated_at"])
            content.seek(0)
            source_bytes = content.read()
    except ProviderUnavailable:
        document.error_category = "PROVIDER_UNAVAILABLE"
        document.status = "quarantined" if document.scan_status != "clean" else "extraction_failed"
        document.save(update_fields=["error_category", "status", "updated_at"])
        if operation:
            operation.state = "pending"
            operation.error_code = "PROVIDER_UNAVAILABLE"
            operation.save(update_fields=["state", "error_code", "updated_at"])
        if job:
            job.status = "provider_unavailable"
            job.error_category = "PROVIDER_UNAVAILABLE"
            job.save(update_fields=["status", "error_category", "updated_at"])
        return document
    ingestion_run = None
    owns_extractor = extractor is None
    try:
        if extractor is None:
            extractor = get_floor_plan_vision_provider(vision_selection)
        event("prepare_document")
        prepared, prepared_type, converted = prepare_document(source_bytes, detected)
        event("extract_text")
        pages = DocumentTextExtractor().extract(document, prepared, media_type=prepared_type)
        event("persist_text", pageCount=len(pages))
        ingestion_run = ingest_document(document, prepared, pages=pages, defer_completion=True)
        event("project_knowledge")
        extraction_result = extract_project_knowledge(
            ingestion_run,
            content=prepared,
            media_type=prepared_type,
            vision_provider=extractor,
        )
        floor_workflow = extraction_result["floorPlanWorkflow"]
        event("collect_images")
        images = collect_document_images(floor_workflow, prepared, prepared_type)
        event("project_vision", imageCount=len(images))
        visual = visual_project_claims(floor_workflow, extractor)
        event("build_outputs")
        output = build_project_output(ingestion_run, pages, images, visual)
        output["sourceConversion"] = {"convertedToPdf": converted, "originalMediaType": detected,
                                      "pageNumberBasis": "converted_pdf" if converted else "original"}
        pending_visual = floor_workflow.status == "needs_provider" or visual["status"] in {"unavailable", "partial_failure"}
        output["processingStatus"] = "awaiting_visual_analysis" if pending_visual else "completed"
        artifacts = store_outputs(ingestion_run, output, floor_workflow.floor_plans.all())
        ingestion_run.summary = ingestion_run.summary | {"projectOutput": output, "outputs": artifacts,
            "convertedToPdf": converted, "processingStatus": output["processingStatus"]}
        ingestion_run.status = "extracting"
        ingestion_run.save(update_fields=["summary", "status", "updated_at"])
        # completed is processing success, NOT publication or registry verification.
        if not pending_visual:
            move_document(document, "completed")
        ReviewTask.objects.get_or_create(project=document.project, task_type="project_output", state="open",
            resolution={"ingestionRunId": str(ingestion_run.id)}, defaults={"priority": 3})
    except Exception as exc:
        error_code = "PROVIDER_UNAVAILABLE" if isinstance(exc, ProviderUnavailable) else (
            str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[A-Z_]{1,64}", str(exc)) else exc.__class__.__name__[:64])
        if ingestion_run:
            ingestion_run.status = "failed"
            ingestion_run.error_code = error_code
            ingestion_run.save(update_fields=["status", "error_code", "updated_at"])
        document.status = "extraction_failed"
        document.error_category = error_code
        document.save(update_fields=["status", "error_category", "updated_at"])
        if job:
            job.status = "provider_unavailable" if isinstance(exc, ProviderUnavailable) else "failed"
            job.error_category = error_code
            job.completed_at = timezone.now()
            job.save(update_fields=["status", "error_category", "completed_at", "updated_at"])
        if operation:
            operation.state = "pending" if isinstance(exc, ProviderUnavailable) else "failed"
            operation.error_code = error_code
            operation.save(update_fields=["state", "error_code", "updated_at"])
        return document
    finally:
        if owns_extractor and hasattr(extractor, "close"):
            extractor.close()
    return _finish_document(document, job, operation, ingestion_run, extraction_result, artifacts, output, pending_visual)


@transaction.atomic
def _finish_document(document, job, operation, ingestion_run, extraction_result, artifacts, output, pending_visual):
    event("finalize")
    materialize_project_amenities(document.project, output)
    ingestion_run.status = "needs_provider" if pending_visual else "completed"
    ingestion_run.completed_at = timezone.now()
    ingestion_run.save(update_fields=["status", "completed_at", "updated_at"])
    candidate_count = len(extraction_result["candidates"])
    floor_workflow = extraction_result["floorPlanWorkflow"]
    floor_plan_ids = list(floor_workflow.floor_plans.values_list("id", flat=True))
    review_scope = (
        Q(candidate__document=document)
        | Q(resolution__ingestionRunId=str(ingestion_run.id))
        | Q(resolution__workflowRunId=str(floor_workflow.id))
    )
    if floor_plan_ids:
        review_scope |= Q(resolution__floorPlanId__in=[str(item) for item in floor_plan_ids])
    requires_review = ReviewTask.objects.filter(project=document.project, state="open").filter(review_scope).exists()
    document.status = "needs_review" if requires_review else "approved"
    document.error_category = ""
    document.save(update_fields=["status", "error_category", "updated_at"])
    if job:
        job.status = document.status
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "completed_at", "updated_at"])
    if operation:
        operation.state = "completed"
        operation.result = {
            "documentId": str(document.id),
            "ingestionRunId": str(ingestion_run.id),
            "candidateCount": candidate_count,
            "floorPlanWorkflowId": str(extraction_result["floorPlanWorkflow"].id),
            "status": document.status,
            "outputs": {name: item["uri"] for name, item in artifacts.items()},
            "processingStatus": output["processingStatus"],
        }
        operation.error_code = ""
        operation.save(update_fields=["state", "result", "error_code", "updated_at"])
    return document


@transaction.atomic
def enqueue_document(document, correlation_id, vision_selection=None):
    selection = resolve_vision_selection() if vision_selection is None else vision_selection
    if document.object_key.startswith("user/media/completed/"):
        move_document(document, "todo")
    return OutboxMessage.objects.create(
        event_type="document.processing.requested",
        schema_version="1.0",
        payload={"documentId": str(document.id), "visionSelection": selection},
        correlation_id=correlation_id,
    )


def document_queue_snapshot(project):
    document_ids = project.documents.values_list("id", flat=True)
    messages = OutboxMessage.objects.filter(
        event_type="document.processing.requested",
        payload__documentId__in=[str(item) for item in document_ids],
    )
    now = timezone.now()
    active = messages.filter(published_at__isnull=True, dead_lettered_at__isnull=True)
    return {
        "pending": active.filter(Q(available_after__isnull=True) | Q(available_after__lte=now)).count(),
        "delayed": active.filter(available_after__gt=now).count(),
        "deadLettered": messages.filter(dead_lettered_at__isnull=False).count(),
        "items": [
            {"messageId": str(message.id), "documentId": message.payload["documentId"],
             "visionSelection": message.payload.get("visionSelection"),
             "state": "dead_lettered" if message.dead_lettered_at else (
                 "delayed" if message.available_after and message.available_after > now else "pending"),
             "attempts": message.attempts, "errorCode": message.last_error_code,
             "availableAfter": message.available_after}
            for message in messages.filter(published_at__isnull=True).order_by("occurred_at")[:100]
        ],
    }


def pending_document_message_ids(project, limit=20):
    document_ids = project.documents.values_list("id", flat=True)
    now = timezone.now()
    return list(
        OutboxMessage.objects.filter(
            event_type="document.processing.requested",
            payload__documentId__in=[str(item) for item in document_ids],
            published_at__isnull=True,
            dead_lettered_at__isnull=True,
        )
        .filter(Q(available_after__isnull=True) | Q(available_after__lte=now))
        .order_by("occurred_at")
        .values_list("id", flat=True)[:limit]
    )


def enqueue_recommendation(requirement_version, snapshot, operation, correlation_id):
    return OutboxMessage.objects.create(
        event_type="recommendation.computation.requested",
        schema_version="1.0",
        payload={
            "requirementVersionId": str(requirement_version.id),
            "snapshotId": str(snapshot.id),
            "operationId": str(operation.id),
        },
        correlation_id=correlation_id,
    )


def process_outbox_message(message_id, scanner=None, extractor=None):
    try:
        with work_lock("outbox", message_id), ExitStack() as locks:
            message = OutboxMessage.objects.filter(id=message_id).first()
            if message is None:
                return "not_ready"
            if message.published_at or InboxMessage.objects.filter(consumer="platform-worker", message_id=message.id).exists():
                return "already_processed"
            if message.attempts > 2 and not message.dead_lettered_at:
                with transaction.atomic():
                    message = _locked_outbox(message.pk)
                    if message.attempts > 2 and not message.published_at:
                        message.dead_lettered_at = timezone.now()
                        message.available_after = None
                        message.last_error_code = message.last_error_code or "RETRY_LIMIT_EXCEEDED"
                        message.save(update_fields=["dead_lettered_at", "available_after", "last_error_code"])
                        operation = Operation.objects.select_for_update().filter(pk=message.payload.get("operationId")).first()
                        if operation and operation.state not in {"completed", "cancelled"}:
                            operation.state = "failed"
                            operation.error_code = message.last_error_code
                            operation.result = operation.result | {"retryable": False}
                            operation.save(update_fields=["state", "error_code", "result", "updated_at"])
            if message.dead_lettered_at or (message.available_after and message.available_after > timezone.now()):
                return "not_ready"
            if message.event_type == "floor_plan.crop_evaluation.requested":
                from .layout_reanalysis import TERMINAL_CROP_ERRORS
                from .models import FloorPlan
                plan = FloorPlan.objects.only("document_id").get(pk=message.payload["floorPlanId"])
                # Worker ownership only. Enqueue never competes for this lock.
                locks.enter_context(work_lock("document", plan.document_id))
                with transaction.atomic():
                    message = _locked_outbox(message.pk)
                    if message.published_at:
                        return "already_processed"
                    if message.dead_lettered_at or (message.available_after and message.available_after > timezone.now()):
                        return "not_ready"
                    operation = Operation.objects.select_for_update().get(pk=message.payload["operationId"])
                    if (operation.state == "cancelled"
                            or (operation.state == "failed" and operation.error_code in TERMINAL_CROP_ERRORS)):
                        # Recover a terminal failure even after a crash before ack.
                        # Serialize against explicit retry; never use a stale state.
                        message.dead_lettered_at = timezone.now()
                        message.available_after = None
                        message.last_error_code = operation.error_code
                        message.save(update_fields=["dead_lettered_at", "available_after", "last_error_code"])
                        return "not_ready"
                    if operation.state != "completed":
                        operation.state = "running"
                        operation.error_code = ""
                        operation.save(update_fields=["state", "error_code", "updated_at"])
                    message.attempts += 1
                    message.save(update_fields=["attempts"])
            else:
                message.attempts += 1
                message.save(update_fields=["attempts"])
            try:
                return _dispatch_outbox_message(message, scanner, extractor)
            except WorkAlreadyClaimed:
                # No work was done. In particular a busy document must not burn
                # the retry budget just because a project queue was polled.
                message.attempts -= 1
                message.available_after = timezone.now() + timedelta(seconds=5)
                message.save(update_fields=["attempts", "available_after"])
                return "not_ready"
            except Exception as exc:
                return _record_outbox_failure(message.pk, exc, expected_attempts=message.attempts)
    except WorkAlreadyClaimed:
        return "not_ready"


def _dispatch_outbox_message(message, scanner=None, extractor=None):
    if message.event_type not in {"document.processing.requested", "floor_plan.crop_evaluation.requested", "source.page_evaluation.requested", "floor_plan.units_evaluation.requested"}:
        return _process_transactional_outbox(message.pk, scanner, extractor)
    if message.event_type == "document.processing.requested":
        document = process_document(message.payload["documentId"], scanner=scanner, extractor=extractor,
                                    vision_selection=message.payload.get("visionSelection"))
        if document.error_category == "PROVIDER_UNAVAILABLE":
            return _record_outbox_failure(message.pk, ProviderUnavailable(), outcome="retry", expected_attempts=message.attempts)
    elif message.event_type == "source.page_evaluation.requested":
        from .source_page_additions import evaluate_source_page
        evaluate_source_page(message.payload)
    elif message.event_type == "floor_plan.units_evaluation.requested":
        from .floor_plan_layout_jobs import evaluate_floor_plan_layouts
        evaluate_floor_plan_layouts(message.payload)
    else:
        from .layout_reanalysis import evaluate_layout_crop
        evaluate_layout_crop(message.payload)
    with transaction.atomic():
        message = _locked_outbox(message.pk)
        if message.event_type == "floor_plan.crop_evaluation.requested":
            operation = Operation.objects.select_for_update().get(pk=message.payload["operationId"])
            if operation.state == "cancelled":
                message.dead_lettered_at = message.dead_lettered_at or timezone.now()
                message.last_error_code = operation.error_code
                message.save(update_fields=["dead_lettered_at", "last_error_code"])
                return "not_ready"
        InboxMessage.objects.get_or_create(consumer="platform-worker", message_id=message.id,
                                          defaults={"result": "processed"})
        message.published_at = timezone.now()
        message.save(update_fields=["published_at"])
    return "processed"


def _locked_outbox(message_id):
    # Like enqueue's plan reservation, avoid SQLite read-to-write upgrades when
    # another short queue transaction commits while a worker is acknowledging.
    if connection.vendor == "sqlite":
        OutboxMessage.objects.filter(pk=message_id).update(id=models.F("id"))
    return OutboxMessage.objects.select_for_update().get(pk=message_id)


@transaction.atomic
def _record_outbox_failure(message_id, exc, outcome="failed", expected_attempts=None):
    """Called under outbox ownership; never increment an already-counted attempt."""
    from .layout_reanalysis import TERMINAL_CROP_ERRORS, layout_evaluation_error
    message = _locked_outbox(message_id)
    if message.published_at or message.dead_lettered_at:
        return outcome
    if expected_attempts is not None and message.attempts != expected_attempts:
        # An explicit retry may have reset the job after evaluator failure but
        # before this bookkeeping step. Do not overwrite that newer request.
        return outcome
    code = layout_evaluation_error(exc)
    terminal = message.event_type == "floor_plan.crop_evaluation.requested" and code in TERMINAL_CROP_ERRORS
    if message.event_type == "floor_plan.crop_evaluation.requested":
        operation = Operation.objects.select_for_update().get(pk=message.payload["operationId"])
        if operation.state == "completed":
            return outcome
        if operation.state == "cancelled":
            code = operation.error_code or code
            terminal = True
        else:
            # The evaluator may have discovered a stale asset while handling an
            # unrelated provider error; keep its exact, checked domain reason.
            if operation.error_code in TERMINAL_CROP_ERRORS:
                code = operation.error_code
                terminal = True
            operation.state = "failed"
            operation.error_code = code
            operation.result = operation.result | {"retryable": not terminal and message.attempts <= 2}
            if code == "MANUAL_CROP_NOT_SINGLE_LAYOUT":
                operation.result = operation.result | {"failureCategory": "BAD_BOUNDARY"}
            operation.save(update_fields=["state", "error_code", "result", "updated_at"])
    message.last_error_code = code
    exhausted = terminal or message.attempts > 2
    message.available_after = None if exhausted else timezone.now() + timedelta(seconds=min(300, 2 ** min(message.attempts, 9)))
    if exhausted:
        message.dead_lettered_at = timezone.now()
        operation = Operation.objects.select_for_update().filter(pk=message.payload.get("operationId")).first()
        if operation and operation.state not in {"completed", "cancelled"}:
            operation.state = "failed"
            operation.error_code = code
            operation.result = operation.result | {"retryable": False}
            operation.save(update_fields=["state", "error_code", "result", "updated_at"])
    message.save(update_fields=["last_error_code", "available_after", "dead_lettered_at"])
    return outcome


@transaction.atomic
def _process_transactional_outbox(message_id, scanner=None, extractor=None):
    message = _locked_outbox(message_id)
    if message.published_at is not None:
        return "already_processed"
    if message.dead_lettered_at or (message.available_after and message.available_after > timezone.now()):
        return "not_ready"
    if InboxMessage.objects.filter(consumer="platform-worker", message_id=message.id).exists():
        message.published_at = timezone.now()
        message.save(update_fields=["published_at"])
        return "already_processed"
    if message.event_type == "document.processing.requested":
        document = process_document(
            message.payload["documentId"], scanner=scanner, extractor=extractor,
            vision_selection=message.payload.get("visionSelection"),
        )
        if document.error_category == "PROVIDER_UNAVAILABLE":
            return "retry"
    elif message.event_type == "recommendation.computation.requested":
        operation = Operation.objects.select_for_update().get(id=message.payload["operationId"])
        operation.state = "running"
        operation.error_code = ""
        operation.save(update_fields=["state", "error_code", "updated_at"])
        try:
            snapshot = compute_recommendations(
                RequirementVersion.objects.get(id=message.payload["requirementVersionId"]),
                RecommendationSnapshot.objects.get(id=message.payload["snapshotId"]),
            )
        except Exception:
            operation.state = "failed"
            operation.error_code = "RECOMMENDATION_COMPUTATION_FAILED"
            operation.save(update_fields=["state", "error_code", "updated_at"])
            raise
        operation.state = "completed"
        operation.result = {"recommendationRequestId": str(snapshot.id)}
        operation.save(update_fields=["state", "result", "updated_at"])
    elif message.event_type == "project.nearby_amenities.requested":
        from .nearby_amenities import enrich_nearby_amenities
        enrich_nearby_amenities(message.payload["projectId"])
    else:
        raise ValueError(f"Unsupported event type: {message.event_type}")
    InboxMessage.objects.create(consumer="platform-worker", message_id=message.id, result="processed")
    message.published_at = timezone.now()
    message.save(update_fields=["published_at"])
    return "processed"


def process_pending_outbox(limit=20, scanner=None, extractor=None, message_ids=None, event_type=None):
    from .queue_monitor import tracked_job
    now = timezone.now()
    if message_ids is None:
        messages = OutboxMessage.objects.filter(published_at__isnull=True, dead_lettered_at__isnull=True)
        if event_type is not None:
            messages = messages.filter(event_type=event_type)
        message_ids = list(
            messages
            .filter(models.Q(available_after__isnull=True) | models.Q(available_after__lte=now))
            .order_by("occurred_at")
            .values_list("id", flat=True)[:limit]
        )
    else:
        message_ids = list(message_ids)[:limit]
    results = {"processed": 0, "retry": 0, "failed": 0}
    for message_id in message_ids:
        try:
            with tracked_job(message_id):
                outcome = process_outbox_message(message_id, scanner=scanner, extractor=extractor)
            if outcome in results:
                results[outcome] += 1
        except Exception:
            # Claim/DB failures are not another model attempt. Failure persistence
            # belongs to the owner above, not a racy unlocked second update here.
            results["failed"] += 1
    return results
