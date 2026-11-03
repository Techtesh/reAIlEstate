from datetime import timedelta
from threading import Event, Lock
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils import timezone

from platform_api.automatic_queue import reserve_ready_jobs, run_automatic_queue, run_reserved_job
from platform_api.jobs.events import RECOMMENDATION_EVENT
from platform_api.models import EvidenceDocument, Operation, Organization, OutboxMessage, Project, QueueWorker


pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def queued(django_user_model):
    builder = django_user_model.objects.create_user(username="uploading-builder")
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Builder company"),
                                     submitted_name="Automatic project", status="verifying")
    document = EvidenceDocument.objects.create(
        project=project, uploaded_by=builder, original_name="brochure.pdf", object_key="test/brochure",
        declared_media_type="application/pdf", byte_length=1, sha256="0" * 64,
        retention_until=timezone.now() + timedelta(days=7), status="quarantined",
    )

    def enqueue(**kwargs):
        return OutboxMessage.objects.create(event_type="document.processing.requested",
            correlation_id=uuid4(), payload={"documentId": str(document.pk)}, **kwargs)

    return project, document, enqueue


def test_reserves_builder_upload_without_staff_owner_or_launch_request(queued, settings):
    settings.INGESTION_MAX_WORKERS = 2
    project, _, enqueue = queued
    message = enqueue()
    reserved = reserve_ready_jobs()
    assert len(reserved) == 1
    worker, message_id = reserved[0]
    assert worker.owner is None and worker.project == project and worker.state == "starting"
    assert message_id == message.pk and worker.message_id == message.pk
    assert reserve_ready_jobs() == []
    message.refresh_from_db()
    assert message.attempts == 0


def test_reserves_and_runs_projectless_recommendation_work(settings):
    settings.INGESTION_MAX_WORKERS = 2
    operation = Operation.objects.create(kind="recommendation", state="pending")
    message = OutboxMessage.objects.create(
        event_type=RECOMMENDATION_EVENT, correlation_id=uuid4(),
        payload={"operationId": str(operation.pk), "snapshotId": str(uuid4()), "requirementVersionId": str(uuid4())},
    )

    worker, message_id = reserve_ready_jobs()[0]

    assert worker.project is None
    assert worker.event_group == "recommendation"
    assert message_id == message.pk
    with patch("platform_api.automatic_queue.process_outbox_message", return_value="processed") as process:
        run_reserved_job(worker, message_id)
    process.assert_called_once_with(message.pk)
    worker.refresh_from_db()
    assert worker.state == "stopped"


def test_capacity_accounts_for_manual_workers_and_refills_on_completion(queued, settings):
    settings.INGESTION_MAX_WORKERS = 2
    _, _, enqueue = queued
    first, second = enqueue(), enqueue()
    manual = QueueWorker.objects.create(state="running")
    reserved = reserve_ready_jobs()
    assert [message_id for _, message_id in reserved] == [first.pk]
    assert reserve_ready_jobs() == []
    manual.state = "stopped"
    manual.save()
    assert [message_id for _, message_id in reserve_ready_jobs()] == [second.pk]


def test_skips_delays_dead_letters_and_completed_work(queued, settings):
    settings.INGESTION_MAX_WORKERS = 8
    _, _, enqueue = queued
    enqueue(available_after=timezone.now() + timedelta(minutes=5))
    enqueue(dead_lettered_at=timezone.now())
    enqueue(published_at=timezone.now())
    ready = enqueue()
    assert [message_id for _, message_id in reserve_ready_jobs()] == [ready.pk]


@pytest.mark.parametrize("linked", [True, False])
def test_cancelled_operations_are_not_dispatched(queued, linked):
    _, document, enqueue = queued
    operation = Operation.objects.create(kind="document_processing", state="cancelled",
                                         result={"documentId": str(document.pk)})
    message = enqueue()
    if linked:
        message.payload["operationId"] = str(operation.pk)
        message.save()
    assert reserve_ready_jobs() == []


def test_scheduled_retry_becomes_eligible_when_due(queued):
    _, _, enqueue = queued
    message = enqueue(available_after=timezone.now() + timedelta(minutes=1))
    assert reserve_ready_jobs() == []
    OutboxMessage.objects.filter(pk=message.pk).update(available_after=timezone.now() - timedelta(seconds=1))
    assert [message_id for _, message_id in reserve_ready_jobs()] == [message.pk]


def test_worker_failure_is_recorded_and_propagated(queued):
    _, _, enqueue = queued
    enqueue()
    worker, message_id = reserve_ready_jobs()[0]
    with patch("platform_api.automatic_queue.process_outbox_message", side_effect=RuntimeError("worker failure")):
        with pytest.raises(RuntimeError, match="worker failure"):
            run_reserved_job(worker, message_id)
    worker.refresh_from_db()
    assert worker.state == "failed" and worker.message_id is None


def test_second_dispatcher_cannot_acquire_singleton_lock():
    from concurrent.futures import ThreadPoolExecutor
    from django.db import connections
    from platform_api.ingestion_runtime import WorkAlreadyClaimed, work_lock

    stop = Event()
    stop.set()

    def second_dispatcher():
        try:
            run_automatic_queue(stop)
        finally:
            connections.close_all()

    with work_lock("automatic-queue", "extraction"), ThreadPoolExecutor(max_workers=1) as executor:
        with pytest.raises(WorkAlreadyClaimed):
            executor.submit(second_dispatcher).result(timeout=5)


def test_upload_arriving_after_idle_is_discovered(queued):
    from threading import Timer

    _, _, enqueue = queued
    stop = Event()
    polls = 0

    def reserve(excluded):
        nonlocal polls
        polls += 1
        if polls == 3:
            enqueue()
        return reserve_ready_jobs(excluded)

    def process(message_id):
        OutboxMessage.objects.filter(pk=message_id).update(published_at=timezone.now())
        stop.set()
        return "processed"

    timeout = Timer(10, stop.set)
    timeout.start()
    try:
        with patch("platform_api.automatic_queue.reserve_ready_jobs", side_effect=reserve), \
                patch("platform_api.automatic_queue.process_outbox_message", side_effect=process) as process_mock:
            run_automatic_queue(stop, poll_seconds=0.02)
            assert process_mock.call_count == 1
    finally:
        stop.set()
        timeout.cancel()
    assert polls >= 3
    assert OutboxMessage.objects.filter(published_at__isnull=False).count() == 1


@pytest.mark.parametrize("state", ["retired", "merged", "inactive_org"])
def test_inactive_projects_are_not_dispatched(queued, state):
    project, _, enqueue = queued
    enqueue()
    if state == "inactive_org":
        project.organization.status = "suspended"
        project.organization.save()
    else:
        project.status = state
        project.save()
    assert reserve_ready_jobs() == []


def test_reserved_work_rechecks_retirement_before_processing(queued):
    project, _, enqueue = queued
    enqueue()
    worker, message_id = reserve_ready_jobs()[0]
    Project.objects.filter(pk=project.pk).update(status="retired")
    with patch("platform_api.automatic_queue.process_outbox_message") as process:
        run_reserved_job(worker, message_id)
    process.assert_not_called()
    worker.refresh_from_db()
    assert worker.state == "stopped"


def test_stale_workers_do_not_block_recovery_but_inflight_messages_are_excluded(queued, settings):
    settings.INGESTION_MAX_WORKERS = 1
    _, _, enqueue = queued
    message = enqueue()
    stale = QueueWorker.objects.create(state="running", message_id=message.pk)
    QueueWorker.objects.filter(pk=stale.pk).update(heartbeat_at=timezone.now() - timedelta(minutes=5))
    assert reserve_ready_jobs([message.pk]) == []
    assert [message_id for _, message_id in reserve_ready_jobs()] == [message.pk]


def test_dispatcher_stays_alive_for_new_work_and_never_exceeds_limit(queued, settings):
    settings.INGESTION_MAX_WORKERS = 2
    _, _, enqueue = queued
    initial = [enqueue() for _ in range(3)]
    stop, guard = Event(), Lock()
    processed, active, peak = [], 0, 0

    def process(message_id):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        stop.wait(0.03)
        OutboxMessage.objects.filter(pk=message_id).update(published_at=timezone.now())
        with guard:
            processed.append(message_id)
            active -= 1
            if len(processed) == len(initial):
                enqueue()
            elif len(processed) == len(initial) + 1:
                stop.set()
        return "processed"

    from threading import Timer
    timeout = Timer(10, stop.set)
    timeout.start()
    try:
        with patch("platform_api.automatic_queue.process_outbox_message", side_effect=process):
            run_automatic_queue(stop, poll_seconds=0.02)
    finally:
        stop.set()
        timeout.cancel()
    assert len(set(processed)) == 4
    assert peak == 2
    assert set(QueueWorker.objects.values_list("state", flat=True)) == {"stopped"}
