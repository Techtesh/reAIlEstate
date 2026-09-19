"""Local queue runner and private, bounded cross-project progress snapshots."""

import logging
import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connections, models, transaction
from django.db.models.fields.json import KT
from django.db.models.functions import Cast, Replace
from django.utils import timezone

from .jobs.events import EXTRACTION_EVENTS
from .models import AuditEvent, EvidenceDocument, FloorPlan, Operation, OutboxMessage, QueueWorker


current_worker = ContextVar("queue_worker", default=None)
logger = logging.getLogger("platform_api.ingestion")
LIVE_SECONDS = 45


def owned_jobs(owner):
    operations = Operation.objects.filter(owner=owner).annotate(
        key=Replace(Cast("pk", models.CharField()), models.Value("-"), models.Value(""))).values("key")
    legacy = AuditEvent.objects.filter(actor=owner, action="document.reprocessing_requested").values("correlation_id")
    return OutboxMessage.objects.filter(event_type__in=EXTRACTION_EVENTS).alias(
        operation_key=Replace(KT("payload__operationId"), models.Value("-"), models.Value(""),
            output_field=models.CharField())).filter(
        models.Q(operation_key__in=operations) | models.Q(correlation_id__in=legacy))


def require_monitor_access(actor, *, run=False):
    permissions = ("platform_api.change_project", "platform_api.change_floorplan") if run else (
        "platform_api.view_project", "platform_api.view_operation", "platform_api.view_outboxmessage")
    if not actor.is_active or not actor.is_staff or not actor.has_perms(permissions):
        raise PermissionDenied


def project_jobs(project):
    documents = [str(key) for key in EvidenceDocument.objects.filter(project=project).values_list("pk", flat=True)]
    plans = [str(key) for key in FloorPlan.objects.filter(project=project).values_list("pk", flat=True)]
    return OutboxMessage.objects.filter(event_type__in=EXTRACTION_EVENTS).filter(
        models.Q(payload__projectId=str(project.pk)) | models.Q(payload__documentId__in=documents)
        | models.Q(payload__floorPlanId__in=plans))


def start_user_queue(actor, project=None):
    from .ingestion_runtime import work_lock
    require_monitor_access(actor, run=True)
    if project and project.status in {"retired", "merged"}:
        raise ValueError("Choose an active project.")
    if not settings.DEBUG or settings.ENVIRONMENT not in {"local", "development"}:
        raise ValueError("Browser worker launch is only available in local development. Start a managed queue worker instead.")
    with work_lock("queue-launch", "local"):
        requested = QueueWorker.objects.filter(project=project)
        if project is None:
            requested = requested.filter(owner=actor)
        existing = requested.filter(models.Q(state="queued") | models.Q(
            state__in=["starting", "idle", "running"],
            heartbeat_at__gte=timezone.now() - timedelta(seconds=LIVE_SECONDS))).first()
        if existing:
            _dispatch_waiting_queues()
            existing.refresh_from_db()
            return existing
        jobs = project_jobs(project) if project else owned_jobs(actor)
        if not jobs.filter(published_at__isnull=True, dead_lettered_at__isnull=True).exists():
            return None
        worker = QueueWorker.objects.create(owner=actor, project=project, state="queued")
        AuditEvent.objects.create(actor=actor, action="queue.requested", entity_type="QueueWorker",
            entity_id=worker.pk, correlation_id=worker.pk,
            metadata={"scope": "project" if project else "own_extraction_jobs",
                      "projectId": str(project.pk) if project else None})
        _dispatch_waiting_queues()
        worker.refresh_from_db()
        return worker


def _dispatch_waiting_queues():
    live = QueueWorker.objects.filter(state__in=["starting", "idle", "running"],
        heartbeat_at__gte=timezone.now() - timedelta(seconds=LIVE_SECONDS)).count()
    capacity = max(0, settings.INGESTION_MAX_WORKERS - live)
    if not capacity:
        return
    for worker in QueueWorker.objects.filter(state="queued").select_related("owner", "project").order_by("created_at"):
        try:
            if worker.owner is None:
                raise PermissionDenied
            require_monitor_access(worker.owner, run=True)
            if worker.project and worker.project.status in {"retired", "merged"}:
                raise PermissionDenied
        except PermissionDenied:
            QueueWorker.objects.filter(pk=worker.pk).update(state="failed")
            continue
        jobs = project_jobs(worker.project) if worker.project else owned_jobs(worker.owner)
        if not jobs.filter(published_at__isnull=True, dead_lettered_at__isnull=True).exists():
            QueueWorker.objects.filter(pk=worker.pk).update(state="stopped")
            continue
        QueueWorker.objects.filter(pk=worker.pk).update(state="starting", heartbeat_at=timezone.now())
        try:
            _launch_worker(worker)
        except OSError:
            QueueWorker.objects.filter(pk=worker.pk).update(state="failed")
            logger.exception("QUEUE LAUNCH FAILED workerId=%s", worker.pk)
            continue
        capacity -= 1
        if not capacity:
            break


def _launch_worker(worker):
    log_path = Path(settings.WORKSPACE_DATA_ROOT) / "output" / f"queue-user-{worker.owner_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(PYTHONUNBUFFERED="1", PYTHONUTF8="1",
        FLOOR_PLAN_EXTRACTION_MODE=settings.FLOOR_PLAN_EXTRACTION_MODE,
        FLOOR_PLAN_VISION_PROFILES=json.dumps(settings.FLOOR_PLAN_VISION_PROFILES),
        FLOOR_PLAN_DEFAULT_VISION_PROFILE=settings.FLOOR_PLAN_DEFAULT_VISION_PROFILE,
        WORKSPACE_DATA_ROOT=str(settings.WORKSPACE_DATA_ROOT),
        INGESTION_MAX_WORKERS=str(settings.INGESTION_MAX_WORKERS))
    command = [sys.executable, str(settings.BASE_DIR / "manage.py"), "process_outbox",
        "--event-type", "extraction", "--owner-id", str(worker.owner_id), "--drain", "--worker-id", str(worker.pk)]
    if worker.project_id:
        command.extend(["--project-id", str(worker.project_id)])
    with log_path.open("a", encoding="utf-8") as output:
        process = subprocess.Popen(command, cwd=settings.BASE_DIR, env=environment,
            stdout=output, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    QueueWorker.objects.filter(pk=worker.pk).update(pid=process.pid)
    AuditEvent.objects.create(actor=worker.owner, action="queue.started", entity_type="QueueWorker",
        entity_id=worker.pk, correlation_id=worker.pk, metadata={"projectId": str(worker.project_id) if worker.project_id else None})


def dispatch_waiting_queues():
    from .ingestion_runtime import work_lock, WorkAlreadyClaimed
    if not settings.DEBUG or settings.ENVIRONMENT not in {"local", "development"}:
        return
    try:
        with work_lock("queue-launch", "local"):
            _dispatch_waiting_queues()
    except WorkAlreadyClaimed:
        return


@contextmanager
def worker_session(event_group, owner=None, worker_id=None, project=None):
    worker = QueueWorker.objects.get(pk=worker_id, owner=owner, project=project) if worker_id else QueueWorker.objects.create(owner=owner, project=project)
    QueueWorker.objects.filter(pk=worker.pk).update(pid=os.getpid(), state="idle", event_group=event_group,
        heartbeat_at=timezone.now(), message_id=None, progress={})
    stop = threading.Event()

    def heartbeat():
        try:
            while not stop.wait(10):
                close_old_connections()
                QueueWorker.objects.filter(pk=worker.pk).update(heartbeat_at=timezone.now())
        finally:
            connections.close_all()

    token = current_worker.set(worker.pk)
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    state = "stopped"
    try:
        yield worker
    except Exception:
        state = "failed"
        raise
    finally:
        stop.set()
        thread.join()
        current_worker.reset(token)
        QueueWorker.objects.filter(pk=worker.pk).update(state=state, heartbeat_at=timezone.now(), message_id=None)
        dispatch_waiting_queues()


@contextmanager
def tracked_job(message_id):
    worker_id = current_worker.get()
    if worker_id:
        QueueWorker.objects.filter(pk=worker_id).update(state="running", message_id=message_id,
            heartbeat_at=timezone.now(), progress={"stage": "claiming"})
        logger.info("QUEUE START messageId=%s workerId=%s", message_id, worker_id)
    try:
        yield
    finally:
        if worker_id:
            QueueWorker.objects.filter(pk=worker_id).update(state="idle", message_id=None,
                heartbeat_at=timezone.now(), progress={})
            logger.info("QUEUE FINISH messageId=%s workerId=%s", message_id, worker_id)


def report_progress(stage, details):
    worker_id = current_worker.get()
    if worker_id:
        allowed = {key: value for key, value in details.items() if key in {
            "pageNumber", "imageIndex", "imageTotal", "renderedPages", "selectedPages", "attempt", "httpStatus"}}
        QueueWorker.objects.filter(pk=worker_id).update(progress={"stage": stage, **allowed}, heartbeat_at=timezone.now())


def delete_dead_letter(actor, message_id, version, *, confirmed=False):
    from uuid import UUID
    from .ingestion_runtime import work_lock

    require_monitor_access(actor)
    require_monitor_access(actor, run=True)
    if not actor.has_perm("platform_api.delete_outboxmessage"):
        raise PermissionDenied
    if not confirmed:
        raise ValidationError("Confirm deletion of this dead-letter job.")
    try:
        message_id = UUID(str(message_id))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("Choose a valid dead-letter job.") from None
    with work_lock("outbox", message_id), transaction.atomic():
        job = OutboxMessage.objects.select_for_update().filter(pk=message_id, event_type__in=EXTRACTION_EVENTS).first()
        if not job or not job.dead_lettered_at or job.published_at:
            raise ValidationError("Only unpublished dead-letter jobs can be deleted. Reload the queue.")
        if job.dead_lettered_at.isoformat() != version:
            raise ValidationError("This job changed. Reload before deleting it.")
        if QueueWorker.objects.filter(message_id=job.pk, state__in=["starting", "running"],
                heartbeat_at__gte=timezone.now() - timedelta(seconds=LIVE_SECONDS)).exists():
            raise ValidationError("A worker still holds this job. Try again after it finishes.")
        AuditEvent.objects.create(actor=actor, action="queue.dead_letter_deleted", entity_type="OutboxMessage",
            entity_id=job.pk, correlation_id=job.correlation_id,
            metadata={"eventType": job.event_type, "attempts": job.attempts, "errorCode": job.last_error_code,
                      "operationId": job.payload.get("operationId")})
        job.delete()


def requeue_dead_letter(actor, message_id, version):
    from uuid import UUID
    from .ingestion_runtime import work_lock

    require_monitor_access(actor, run=True)
    try:
        message_id = UUID(str(message_id))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("Choose a valid dead-letter job.") from None
    with work_lock("outbox", message_id), transaction.atomic():
        job = OutboxMessage.objects.select_for_update().filter(pk=message_id, event_type__in=EXTRACTION_EVENTS).first()
        if not job or not job.dead_lettered_at or job.published_at:
            raise ValidationError("Only unpublished dead-letter jobs can be requeued. Reload the queue.")
        if job.dead_lettered_at.isoformat() != version:
            raise ValidationError("This job changed. Reload before requeueing it.")
        if QueueWorker.objects.filter(message_id=job.pk, state__in=["starting", "running"],
                heartbeat_at__gte=timezone.now() - timedelta(seconds=LIVE_SECONDS)).exists():
            raise ValidationError("A worker still holds this job. Try again after it finishes.")
        operation = None
        if job.payload.get("operationId"):
            operation = Operation.objects.select_for_update().filter(pk=job.payload["operationId"]).first()
            if operation and operation.state in {"completed", "cancelled", "running"}:
                raise ValidationError(f"A {operation.state} operation cannot be requeued.")
            if operation:
                operation.state = "pending"
                operation.error_code = ""
                operation.result = {key: value for key, value in operation.result.items()
                                    if key not in {"failureCategory", "retryable"}}
                operation.save(update_fields=["state", "error_code", "result", "updated_at"])
        previous = {"attempts": job.attempts, "errorCode": job.last_error_code,
                    "deadLetteredAt": job.dead_lettered_at.isoformat()}
        job.dead_lettered_at = None
        job.available_after = None
        job.last_error_code = ""
        job.attempts = 0
        job.save(update_fields=["dead_lettered_at", "available_after", "last_error_code", "attempts"])
        AuditEvent.objects.create(actor=actor, action="queue.dead_letter_requeued", entity_type="OutboxMessage",
            entity_id=job.pk, correlation_id=job.correlation_id,
            metadata={"eventType": job.event_type, "operationId": job.payload.get("operationId"), **previous})
        return job


def queue_snapshot(actor):
    require_monitor_access(actor)
    now = timezone.now()
    active_workers = QueueWorker.objects.filter(state__in=["starting", "idle", "running"],
        heartbeat_at__gte=now - timedelta(seconds=LIVE_SECONDS)).count()
    workers = list(QueueWorker.objects.select_related("project").order_by("-heartbeat_at")[:50])
    live = [worker for worker in workers if worker.state in {"starting", "idle", "running"}
            and worker.heartbeat_at >= now - timedelta(seconds=LIVE_SECONDS)]
    running = {str(worker.message_id): worker for worker in live if worker.message_id}
    jobs = OutboxMessage.objects.filter(event_type__in=EXTRACTION_EVENTS)
    pending = jobs.filter(published_at__isnull=True, dead_lettered_at__isnull=True)
    rows = list(jobs.filter(pk__in=running))
    rows += list(pending.exclude(pk__in=running).order_by("occurred_at")[:50])
    rows += list(jobs.filter(published_at__isnull=False).order_by("-published_at")[:25])
    rows += list(jobs.filter(dead_lettered_at__isnull=False).order_by("-dead_lettered_at")[:25])
    rows = list({job.pk: job for job in rows}.values())
    can_requeue = actor.has_perms(["platform_api.change_project", "platform_api.change_floorplan"])
    can_delete = actor.has_perms(["platform_api.change_project", "platform_api.change_floorplan",
                                 "platform_api.delete_outboxmessage"])
    plans = {str(plan.pk): plan for plan in FloorPlan.objects.filter(
        pk__in=[job.payload["floorPlanId"] for job in rows if job.payload.get("floorPlanId")]).select_related("project")}
    documents = {str(document.pk): document for document in EvidenceDocument.objects.filter(
        pk__in=[job.payload["documentId"] for job in rows if job.payload.get("documentId")]).select_related("project")}
    items = []
    for job in rows:
        plan = plans.get(job.payload.get("floorPlanId"))
        document = documents.get(job.payload.get("documentId"))
        project = plan.project if plan else document.project if document else None
        worker = running.get(str(job.pk))
        state = "dead_letter" if job.dead_lettered_at and not job.published_at else "completed" if job.published_at else "running" if worker else (
            "retrying" if job.available_after and job.available_after > now else "queued")
        items.append({"id": str(job.pk), "kind": job.event_type.removesuffix(".requested"), "state": state,
            "project": project.submitted_name if project else "Unknown project",
            "projectId": str(project.pk) if project else None,
            "label": plan.display_title if plan else document.original_name if document else "Extraction",
            "attempts": job.attempts, "error": job.last_error_code,
            "updatedAt": (job.published_at or job.dead_lettered_at or job.occurred_at).isoformat(),
            "retryAt": job.available_after.isoformat() if job.available_after else None,
            "canRequeue": can_requeue and state == "dead_letter" and worker is None,
            "canDelete": can_delete and state == "dead_letter" and worker is None,
            "deleteVersion": job.dead_lettered_at.isoformat() if state == "dead_letter" else None,
            "progress": worker.progress if worker else {}})
    return {"updatedAt": now.isoformat(),
        "capacity": {"limit": settings.INGESTION_MAX_WORKERS, "active": active_workers,
                     "available": max(0, settings.INGESTION_MAX_WORKERS - active_workers)},
        "counts": {"pending": pending.count(), "running": len(running),
        "completed": jobs.filter(published_at__isnull=False).count(),
        "stopped": jobs.filter(dead_lettered_at__isnull=False).count(),
        "dead_letter": jobs.filter(dead_lettered_at__isnull=False, published_at__isnull=True).count()},
        "workers": [{"id": str(worker.pk), "pid": worker.pid, "group": worker.event_group,
            "project": worker.project.submitted_name if worker.project else "All eligible projects",
            "state": worker.state if worker in live or worker.state in {"queued", "stopped", "failed"} else "stale",
            "heartbeatAt": worker.heartbeat_at.isoformat()} for worker in workers], "jobs": items}