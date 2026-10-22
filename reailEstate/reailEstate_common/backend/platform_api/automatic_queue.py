"""Capacity-aware polling for background work, independent of browser sessions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import logging
from uuid import UUID

from django.conf import settings
from django.db import close_old_connections, connections
from django.db.models import Q
from django.utils import timezone

from .ingestion_runtime import WorkAlreadyClaimed, work_lock
from .jobs.events import EXTRACTION_EVENTS, RECOMMENDATION_EVENT
from .models import EvidenceDocument, FloorPlan, Operation, OutboxMessage, Project, QueueWorker
from .queue_monitor import LIVE_SECONDS, tracked_job, worker_session
from .workers import process_outbox_message


logger = logging.getLogger(__name__)
AUTOMATIC_EVENTS = (*EXTRACTION_EVENTS, RECOMMENDATION_EVENT)


def job_project(message):
    """Resolve saved provenance rather than trust a supplied project identifier."""
    if message.event_type == RECOMMENDATION_EVENT:
        return None
    for key, model in (("documentId", EvidenceDocument), ("floorPlanId", FloorPlan), ("projectId", Project)):
        value = message.payload.get(key)
        if value:
            try:
                identifier = UUID(str(value))
            except (ValueError, TypeError, AttributeError):
                logger.warning("Invalid project provenance for queued message %s", message.pk)
                return None
            record = model.objects.filter(pk=identifier).first()
            if record is None:
                logger.warning("Missing project provenance for queued message %s", message.pk)
                return None
            return record if model is Project else record.project
    logger.warning("No project provenance for queued message %s", message.pk)
    return None


def reserve_ready_jobs(excluded=()):
    """Reserve slots under the same lock used by manual local worker launches."""
    excluded = tuple(excluded)
    try:
        with work_lock("queue-launch", "local"):
            live = QueueWorker.objects.filter(
                state__in=["starting", "idle", "running"],
                heartbeat_at__gte=timezone.now() - timedelta(seconds=LIVE_SECONDS),
            )
            capacity = max(0, settings.INGESTION_MAX_WORKERS - max(live.count(), len(excluded)))
            if not capacity:
                return []
            candidates = OutboxMessage.objects.filter(
                event_type__in=AUTOMATIC_EVENTS, published_at__isnull=True, dead_lettered_at__isnull=True,
            ).filter(Q(available_after__isnull=True) | Q(available_after__lte=timezone.now())).exclude(
                pk__in=live.exclude(message_id=None).values("message_id"),
            ).exclude(pk__in=excluded).order_by("occurred_at", "pk")
            reserved = []
            for message in candidates.iterator():
                project = job_project(message)
                if message.event_type != RECOMMENDATION_EVENT and project is None:
                    continue
                if project and (project.status in {"retired", "merged"} or project.organization.status != "active"):
                    continue
                if project and Project.objects.filter(pk=project.pk, merge_out__state="applied").exists():
                    continue
                if job_cancelled(message):
                    continue
                worker = QueueWorker.objects.create(
                    project=project, state="starting",
                    event_group="recommendation" if message.event_type == RECOMMENDATION_EVENT else "extraction",
                    message_id=message.pk,
                )
                reserved.append((worker, message.pk))
                if len(reserved) == capacity:
                    break
            return reserved
    except WorkAlreadyClaimed:
        return []


def job_cancelled(message):
    if message.payload.get("operationId"):
        return Operation.objects.filter(pk=message.payload["operationId"], state="cancelled").exists()
    if message.event_type == "document.processing.requested":
        state = Operation.objects.filter(
            kind="document_processing", result__documentId=message.payload.get("documentId"),
        ).order_by("-created_at").values_list("state", flat=True).first()
        return state == "cancelled"
    return False


def run_reserved_job(worker, message_id):
    close_old_connections()
    try:
        project = Project.objects.get(pk=worker.project_id) if worker.project_id else None
        with worker_session(worker.event_group, worker_id=worker.pk, project=project):
            if project and (project.status in {"retired", "merged"} or project.organization.status != "active"
                    or Project.objects.filter(pk=project.pk, merge_out__state="applied").exists()):
                return
            message = OutboxMessage.objects.filter(pk=message_id).first()
            if message is None or job_cancelled(message):
                return
            with tracked_job(message_id):
                process_outbox_message(message_id)
    finally:
        connections.close_all()


def run_automatic_queue(stop, *, poll_seconds=2):
    # Keep the lock's connection alive until shutdown; closing it would drop the singleton lock.
    with work_lock("automatic-queue", "extraction"), ThreadPoolExecutor(
        max_workers=settings.INGESTION_MAX_WORKERS, thread_name_prefix="extraction",
    ) as executor:
        running = {}
        while not stop.is_set():
            for future in list(running):
                if future.done():
                    future.result()
                    del running[future]
            for worker, message_id in reserve_ready_jobs(running.values()):
                running[executor.submit(run_reserved_job, worker, message_id)] = message_id
            stop.wait(poll_seconds)
        for future in running:
            future.result()
