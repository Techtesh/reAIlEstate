import io
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from django.db.models import Q

from platform_api.jobs.events import (
    CROP_EVENT, DOCUMENT_EVENT, EXTRACTION_EVENTS, MEDIA_EVENTS, OUTBOX_EVENT_GROUPS,
    PAGE_EVENT, RECOMMENDATION_EVENT, UNITS_EVENT,
)
from platform_api.management.commands import process_outbox


EMPTY_RESULTS = {"processed": 0, "retry": 0, "failed": 0}


@pytest.fixture(autouse=True)
def isolated_worker_session(monkeypatch):
    from platform_api import queue_monitor

    monkeypatch.setattr(queue_monitor, "worker_session", Mock(side_effect=lambda *args, **kwargs: nullcontext()))
    monkeypatch.setattr(queue_monitor, "dispatch_waiting_queues", Mock())


def run_command(*, output=None, **overrides):
    command = process_outbox.Command(stdout=output or io.StringIO())
    options = vars(command.create_parser("manage.py", "process_outbox").parse_args([]))
    command.handle(**(options | overrides))


def test_shared_event_contracts():
    assert DOCUMENT_EVENT == "document.processing.requested"
    assert CROP_EVENT == "floor_plan.crop_evaluation.requested"
    assert PAGE_EVENT == "source.page_evaluation.requested"
    assert UNITS_EVENT == "floor_plan.units_evaluation.requested"
    assert RECOMMENDATION_EVENT == "recommendation.computation.requested"
    assert set(MEDIA_EVENTS) == {CROP_EVENT, PAGE_EVENT, UNITS_EVENT}
    assert len(MEDIA_EVENTS) == 3
    assert set(EXTRACTION_EVENTS) == {DOCUMENT_EVENT, *MEDIA_EVENTS}
    assert OUTBOX_EVENT_GROUPS["documents"] == (DOCUMENT_EVENT,)
    assert OUTBOX_EVENT_GROUPS["all"] is None


@pytest.mark.parametrize("selector", ["all", "documents", "recommendations", "media"])
def test_command_parser_accepts_existing_and_media_selectors(selector):
    command = process_outbox.Command()
    parser = command.create_parser("manage.py", "process_outbox")
    options = parser.parse_args(["--event-type", selector, "--once"])
    assert options.event_type == selector
    assert options.once is True
    assert parser.parse_args([]).event_type == "all"


@pytest.mark.parametrize("selector, expected", [
    ("all", None), ("documents", (DOCUMENT_EVENT,)), ("recommendations", (RECOMMENDATION_EVENT,)),
])
def test_existing_selectors_keep_worker_semantics(selector, expected, monkeypatch):
    worker = Mock(return_value=EMPTY_RESULTS)
    monkeypatch.setattr(process_outbox, "process_pending_outbox", worker)
    manager, pending, _, _ = mock_queue(monkeypatch, [["eligible-job"]])
    sleep = Mock()
    monkeypatch.setattr(process_outbox.time, "sleep", sleep)
    run_command(
        limit=20, poll_seconds=2, once=True, event_type=selector,
    )
    worker.assert_called_once_with(limit=20, message_ids=["eligible-job"])
    manager.all.return_value.filter.assert_called_once_with(published_at__isnull=True, dead_lettered_at__isnull=True)
    if expected is None:
        assert pending.filter.call_count == 1
        assert pending.filter.call_args.kwargs == {}
    else:
        assert pending.filter.call_args_list[0] == call(event_type__in=expected)
    sleep.assert_not_called()


def mock_queue(monkeypatch, batches):
    manager, eligible, ready, ordered = Mock(), Mock(), Mock(), Mock()
    manager.all.return_value.filter.return_value = eligible
    eligible.filter.side_effect = lambda *args, **kwargs: eligible if "event_type__in" in kwargs else ready
    ready.order_by.return_value = ordered
    ordered.values_list.side_effect = batches
    monkeypatch.setattr(process_outbox, "OutboxMessage", SimpleNamespace(objects=manager))
    return manager, eligible, ready, ordered


def test_media_filters_eligible_jobs_and_limits_one_combined_batch(monkeypatch):
    manager, eligible, ready, ordered = mock_queue(monkeypatch, [["page", "crop", "units"]])
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    monkeypatch.setattr(process_outbox.timezone, "now", Mock(return_value=now))
    worker = Mock(return_value={"processed": 1, "retry": 1, "failed": 0})
    monkeypatch.setattr(process_outbox, "process_pending_outbox", worker)
    output = io.StringIO()
    run_command(output=output, limit=2, poll_seconds=2, once=True, event_type="media")
    manager.all.return_value.filter.assert_called_once_with(
        published_at__isnull=True, dead_lettered_at__isnull=True,
    )
    assert eligible.filter.call_args_list == [call(event_type__in=MEDIA_EVENTS),
        call(Q(available_after__isnull=True) | Q(available_after__lte=now))]
    ready.order_by.assert_called_once_with("occurred_at")
    ordered.values_list.assert_called_once_with("id", flat=True)
    worker.assert_called_once_with(limit=2, message_ids=["page", "crop"])
    assert output.getvalue().strip() == "processed=1 retry=1 failed=0"


def test_media_empty_batch_does_not_fall_back_to_unfiltered_worker(monkeypatch):
    mock_queue(monkeypatch, [[]])
    worker = Mock(return_value=EMPTY_RESULTS)
    monkeypatch.setattr(process_outbox, "process_pending_outbox", worker)
    run_command(limit=20, poll_seconds=2, once=True, event_type="media")
    worker.assert_called_once_with(limit=20, message_ids=[])


def test_media_poll_refreshes_availability_and_preserves_sleep_floor(monkeypatch):
    manager, eligible, _, _ = mock_queue(monkeypatch, [[], ["newly-ready"]])
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    later = now + timedelta(seconds=1)
    clock = Mock(side_effect=[now, later])
    monkeypatch.setattr(process_outbox.timezone, "now", clock)
    worker = Mock(side_effect=[EMPTY_RESULTS, KeyboardInterrupt])
    monkeypatch.setattr(process_outbox, "process_pending_outbox", worker)
    sleep = Mock()
    monkeypatch.setattr(process_outbox.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        run_command(limit=0, poll_seconds=0, once=False, event_type="media")
    assert manager.all.return_value.filter.call_count == 2
    assert eligible.filter.call_args_list == [
        call(event_type__in=MEDIA_EVENTS),
        call(Q(available_after__isnull=True) | Q(available_after__lte=now)),
        call(event_type__in=MEDIA_EVENTS),
        call(Q(available_after__isnull=True) | Q(available_after__lte=later)),
    ]
    assert worker.call_args_list == [
        call(limit=1, message_ids=[]), call(limit=1, message_ids=["newly-ready"]),
    ]
    sleep.assert_called_once_with(0.1)


def test_media_batch_retains_limit_ceiling(monkeypatch):
    mock_queue(monkeypatch, [list(range(600))])
    worker = Mock(return_value=EMPTY_RESULTS)
    monkeypatch.setattr(process_outbox, "process_pending_outbox", worker)
    run_command(limit=1000, poll_seconds=2, once=True, event_type="media")
    worker.assert_called_once_with(limit=500, message_ids=list(range(500)))