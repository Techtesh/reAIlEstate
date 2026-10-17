"""Offline queue-only and cached-payload regression tests; no live providers."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.core.exceptions import PermissionDenied
from django.db import OperationalError, connection, transaction
from django.http import Http404
from django.utils import timezone

from platform_api import layout_reanalysis as layouts
from platform_api import workers
from platform_api.artifact_storage import storage
from platform_api.ingestion_runtime import work_lock
from platform_api.integrations import ProviderUnavailable
from platform_api.layout_evaluation_status import layout_evaluation_states, queue_single_layout, run_single_layout
from platform_api.models import AuditEvent, FloorPlan, FloorPlanAsset, InboxMessage, Operation, OutboxMessage, Project, ProjectMerge
from .test_floor_plan_layout_jobs import source


pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def layout(source):
    plan, actor = source
    plan.plan_scope = "flat"
    plan.display_name = "Reviewer alias"
    plan.raw_extraction = {"draftId": "test-draft", "cropRevision": 4, "extractionStale": True}
    plan.review_state = "manual_crop_needs_evaluation"
    plan.save()
    return plan, actor


@pytest.fixture
def inference(monkeypatch):
    candidate = SimpleNamespace(
        plan_scope="flat", plan_name="Model name", configuration_type="2BHK", carpet_area_sqft=650,
        orientation="north", dimensions=[{"room": "bedroom", "length": 10, "width": 12}],
        features={"balcony": True}, confidence=0.9,
        dimension_metadata={},
    )
    agent = Mock()
    agent._reconcile.return_value = [candidate]
    agent._room_count.return_value = 2
    agent._provider_metadata.return_value = {"provider": "mock"}
    agent.version = "mock-1.0"
    monkeypatch.setattr(layouts, "FloorPlanAgent", Mock(return_value=agent))
    result = {"pageType": "single_unit_plan", "visualEvidence": "One dwelling"}

    def analyze(*_args):
        assert not connection.in_atomic_block, "Model work cannot hold a database transaction"
        return result

    provider = Mock(spec=["analyze_layout", "close"])
    provider.analyze_layout.side_effect = analyze
    factory = Mock(return_value=provider)
    monkeypatch.setattr(layouts, "get_floor_plan_vision_provider", factory)
    return provider, factory, agent, result


def sibling(plan):
    return FloorPlan.objects.create(
        project=plan.project, document=plan.document, asset=plan.asset, workflow_run=plan.workflow_run,
        page_number=1, plan_scope="flat", plan_name="Sibling with shared image", extractor_version="test",
    )


def test_layout_evaluation_persists_foyer_and_internal_passage_measurements(layout, monkeypatch):
    from platform_api.floor_plan_vision import OpenAIFloorPlanVisionProvider as get_floor_plan_vision_provider, resolve_ai_vision_selection
    from platform_api.project_outputs import layout_metrics
    from .test_ai_plan_classification import layout_result
    from .test_room_dimensions import native_room
    plan, actor = layout
    result = layout_result()
    result["floorPlans"][0]["dimensions"] = [
        native_room("Foyer", "7'9\"", "3'11\""),
        native_room("Internal passage", "3'5\"", "10'7\""),
    ]
    selection = resolve_ai_vision_selection("pinned")
    provider = get_floor_plan_vision_provider(selection)
    provider.analyze = Mock(return_value=result)
    monkeypatch.setattr(layouts, "get_floor_plan_vision_provider", lambda _: provider)
    message, _ = layouts.queue_layout_evaluation(plan, actor, vision_selection=selection)
    layouts.evaluate_layout_crop(message.payload)
    plan.refresh_from_db()
    assert [room["room"] for room in plan.dimensions] == ["Foyer", "Internal passage"]
    assert [room["areaSqFt"] for room in plan.dimensions] == [30.35, 36.16]
    assert plan.dimensions[0]["lengthFt"] == 7.75
    assert plan.dimensions[1]["widthFt"] == pytest.approx(10.583333)
    assert plan.display_name == "Reviewer alias"
    assert plan.review_state == "gen_with_ai_needs_review"
    metrics = layout_metrics(plan, {"effectiveParcel": {"value": None}})
    assert [room["room"] for room in metrics["rooms"]] == ["Foyer", "Internal passage"]
    assert metrics["roomRectangleSumSqFt"] == 66.51


def test_guessed_measurements_warn_without_boundary_failure_and_fresh_readings_clear_warning(layout, client, monkeypatch):
    from django.urls import reverse
    from .test_ai_plan_classification import layout_result
    from .test_room_dimensions import native_room
    from platform_api.floor_plan_vision import OpenAIFloorPlanVisionProvider as get_floor_plan_vision_provider, resolve_ai_vision_selection
    plan, actor = layout
    result = layout_result()
    result["floorPlans"][0]["dimensions"] = [native_room("Living", 7, 3.87, "m", unitSource="inferred"),
        native_room("Foyer", 7.75, None)]
    selection = resolve_ai_vision_selection("pinned")
    provider = get_floor_plan_vision_provider(selection)
    provider.analyze = Mock(return_value=result)
    monkeypatch.setattr(layouts, "get_floor_plan_vision_provider", lambda _: provider)
    message, _ = layouts.queue_layout_evaluation(plan, actor, vision_selection=selection)
    layouts.evaluate_layout_crop(message.payload)
    plan.refresh_from_db()
    assert "Living: AI-inferred unit" in plan.raw_extraction["dimensionWarning"]
    assert plan.review_state == "gen_with_ai_needs_review" and not plan.raw_extraction.get("qualityWarning")
    assert plan.dimensions[1]["widthFt"] is None and plan.dimensions[1]["areaSqFt"] is None
    client.force_login(actor)
    page = client.get(reverse("admin:platform_api_project_media_dashboard", args=[plan.project_id]), {"section": "layouts"})
    assert b"Room size review required:" in page.content
    assert b"Living: AI-inferred unit" in page.content and b"22.97 ft" in page.content
    assert b"Boundary review required:" not in page.content
    result["floorPlans"][0]["dimensions"] = [native_room("Living", 7, 3.87, "m")]
    message, _ = layouts.queue_layout_evaluation(plan, actor, vision_selection=selection, force=True)
    layouts.evaluate_layout_crop(message.payload)
    plan.refresh_from_db()
    assert plan.raw_extraction["dimensionWarning"] == ""
    assert len(plan.raw_extraction["dimensionEvidence"]) == 1


@pytest.mark.parametrize("mode", ["new", "retry-failed", "force"])
@pytest.mark.parametrize("state", ["pending", "failed", "completed", "cancelled"])
def test_bulk_evaluation_modes_preserve_queue_contract(layout, inference, mode, state):
    from platform_api.bulk_layout_evaluations import queue_layout_batch

    plan, actor = layout
    message, operation = layouts.queue_layout_evaluation(plan, actor)
    operation.state = state
    operation.save()
    if state in {"failed", "cancelled"}:
        message.dead_lettered_at = timezone.now()
        message.attempts = 3
    if state == "completed":
        message.published_at = timezone.now()
    message.save()
    original = (message.dead_lettered_at, message.attempts, message.published_at)
    outcome, = queue_layout_batch([plan], vision_selection=message.payload["visionSelection"], mode=mode, actor=actor)
    message.refresh_from_db()
    operation.refresh_from_db()
    if state == "failed" and mode != "new":
        assert operation.state == "pending" and outcome.status == "queued"
        assert message.dead_lettered_at is None and message.attempts == 0
        assert AuditEvent.objects.filter(action="floor_plan.crop_evaluation_retried", actor=actor).exists()
    else:
        assert operation.state == state
        assert (message.dead_lettered_at, message.attempts, message.published_at) == original
    assert OutboxMessage.objects.count() == (2 if state == "completed" and mode == "force" else 1)
    assert Operation.objects.exclude(owner=actor).count() == 0
    inference[1].assert_not_called()


def test_bulk_command_preview_scope_and_new_mode(layout, settings, inference):
    from io import StringIO
    from uuid import uuid4
    from django.core.management import call_command
    from django.core.management.base import CommandError
    from platform_api.bulk_layout_evaluations import eligible_layouts

    plan, _ = layout
    settings.DEBUG = True
    settings.ENVIRONMENT = "local"
    output = StringIO()
    call_command("requeue_all_layout_evaluations", mode="new", project_id=str(plan.project_id), stdout=output)
    assert "eligible=1" in output.getvalue() and "Preview only" in output.getvalue()
    assert not OutboxMessage.objects.exists()
    assert not eligible_layouts(project_id=uuid4()).exists()
    with pytest.raises(CommandError, match="at least 1"):
        call_command("requeue_all_layout_evaluations", limit=0)
    call_command("requeue_all_layout_evaluations", mode="new", execute=True, project_id=str(plan.project_id))
    assert OutboxMessage.objects.count() == 1
    call_command("requeue_all_layout_evaluations", mode="new", execute=True, project_id=str(plan.project_id))
    assert OutboxMessage.objects.count() == 1
    plan.crop_remaining = True
    plan.save()
    assert not eligible_layouts().exists()
    settings.ENVIRONMENT = "production"
    with pytest.raises(CommandError, match="restricted"):
        call_command("requeue_all_layout_evaluations", execute=True)
    inference[1].assert_not_called()


def test_queue_is_durable_idempotent_and_does_not_read_images_or_run_workers(layout, inference, monkeypatch):
    plan, actor = layout
    _, factory, _, _ = inference
    runner = Mock(side_effect=AssertionError("Request executed a worker"))
    monkeypatch.setattr(workers, "process_pending_outbox", runner)
    monkeypatch.setattr(storage, "open", Mock(side_effect=AssertionError("Enqueue read an image")))
    before = FloorPlan.objects.filter(pk=plan.pk).values().get()
    first = queue_single_layout(plan.project, plan.pk, actor)
    repeated = run_single_layout(plan.project, plan.pk, actor)
    assert first == repeated
    assert first["status"] == "queued"
    job = OutboxMessage.objects.get(pk=first["messageId"])
    assert job.payload["assetSha256"] == plan.asset.sha256
    assert job.attempts == 0 and job.published_at is None
    assert FloorPlan.objects.filter(pk=plan.pk).values().get() == before
    assert Operation.objects.count() == OutboxMessage.objects.count() == 1
    assert AuditEvent.objects.filter(action="floor_plan.crop_evaluation_queued").count() == 1
    runner.assert_not_called()
    factory.assert_not_called()


def test_nested_crop_transaction_and_document_lock_do_not_block_enqueue(layout, inference):
    plan, actor = layout
    other = sibling(plan)
    with work_lock("document", plan.document_id), transaction.atomic():
        first, operation = layouts.queue_layout_evaluation(plan, actor)
        again, same_operation = layouts.queue_layout_evaluation(plan, actor)
        second, _ = layouts.queue_layout_evaluation(other, actor)
    assert first.pk == again.pk and operation.pk == same_operation.pk
    assert second.pk != first.pk
    assert OutboxMessage.objects.count() == 2
    inference[1].assert_not_called()


def test_running_model_allows_same_and_other_layout_clicks(layout, inference):
    plan, actor = layout
    other = sibling(plan)
    job, operation = layouts.queue_layout_evaluation(plan, actor)
    provider, _, _, result = inference

    def analyze(*_args):
        assert not connection.in_atomic_block
        repeated = queue_single_layout(plan.project, plan.pk, actor, retry=True, force=True)
        assert repeated["status"] == "running" and repeated["messageId"] == str(job.pk)
        queued = queue_single_layout(plan.project, other.pk, actor)
        assert queued["status"] == "queued" and queued["messageId"] != str(job.pk)
        return result

    provider.analyze_layout.side_effect = analyze
    assert workers.process_outbox_message(job.pk) == "processed"
    operation.refresh_from_db()
    plan.refresh_from_db()
    assert operation.state == "completed"
    assert plan.display_name == "Reviewer alias"
    assert plan.raw_extraction["cropRevision"] == 4
    # No blanket stale-flag reset; extraction metadata identifies the checked image.
    assert plan.raw_extraction["extractionStale"] is True
    assert plan.raw_extraction["cropEvaluation"]["assetSha256"] == job.payload["assetSha256"]
    assert OutboxMessage.objects.count() == 2
    provider.analyze_layout.assert_called_once()


def test_busy_document_does_not_spend_attempt_and_requests_still_queue(layout):
    if connection.vendor == "postgresql":
        pytest.skip("Same-session PostgreSQL advisory locks are reentrant; this exercises the file-lock fallback")
    plan, actor = layout
    with work_lock("document", plan.document_id):
        result = queue_single_layout(plan.project, plan.pk, actor)
        assert workers.process_outbox_message(result["messageId"]) == "not_ready"
    message = OutboxMessage.objects.get(pk=result["messageId"])
    assert message.attempts == 0
    assert message.available_after > timezone.now()


def test_exhausted_fallback_persists_safe_attempts_and_terminal_state(layout):
    from platform_api.vision_errors import AllVisionProvidersFailed
    plan, actor = layout
    message, operation = layouts.queue_layout_evaluation(plan, actor)
    OutboxMessage.objects.filter(pk=message.pk).update(attempts=3)
    attempts = [{"profileId": "local", "outcome": "failed", "errorCode": "VISION_TIMEOUT"}]
    workers._record_outbox_failure(message.pk, AllVisionProvidersFailed(attempts), expected_attempts=3)
    message.refresh_from_db()
    operation.refresh_from_db()
    assert message.dead_lettered_at and message.last_error_code == "VISION_ALL_PROVIDERS_FAILED"
    assert operation.state == "failed" and operation.error_code == message.last_error_code
    assert operation.result["visionAttempts"] == attempts and operation.result["retryable"] is False


@pytest.mark.parametrize("acknowledged", [False, True])
def test_completed_is_preserved_until_explicit_force_and_force_itself_deduplicates(layout, acknowledged):
    plan, actor = layout
    job, operation = layouts.queue_layout_evaluation(plan, actor)
    Operation.objects.filter(pk=operation.pk).update(state="completed", result={"saved": "result"})
    if acknowledged:
        OutboxMessage.objects.filter(pk=job.pk).update(published_at=timezone.now())
    same, saved = layouts.queue_layout_evaluation(plan, actor, retry=True)
    assert same.pk == job.pk and saved.result == {"saved": "result"}
    assert saved.state == "completed"
    forced, new_operation = layouts.queue_layout_evaluation(plan, actor, force=True)
    assert forced.pk != job.pk and new_operation.state == "pending"
    assert layouts.queue_layout_evaluation(plan, actor, force=True)[0].pk == forced.pk
    operation.refresh_from_db()
    assert operation.result == {"saved": "result"} and operation.state == "completed"


def test_failed_retry_is_explicit_and_retains_pin(layout):
    plan, actor = layout
    job, operation = layouts.queue_layout_evaluation(plan, actor)
    pin = job.payload["visionSelection"]
    due = timezone.now() + timedelta(days=1)
    OutboxMessage.objects.filter(pk=job.pk).update(
        attempts=5, dead_lettered_at=timezone.now(), available_after=due, last_error_code="RuntimeError",
    )
    Operation.objects.filter(pk=operation.pk).update(state="failed", error_code="RuntimeError")
    same, failed = layouts.queue_layout_evaluation(plan, actor, force=True)
    assert same.pk == job.pk and same.attempts == 5 and same.available_after == due
    assert failed.state == "failed"
    retried, pending = layouts.queue_layout_evaluation(plan, actor, vision_profile_id="other", retry=True)
    assert retried.pk == job.pk and pending.pk == operation.pk
    assert pending.state == "pending" and pending.error_code == ""
    assert retried.attempts == 0 and retried.dead_lettered_at is None and retried.available_after is None
    assert retried.payload["visionSelection"] == pin
    assert OutboxMessage.objects.count() == 1


def test_pending_backoff_is_not_reset_by_clicks_even_explicit_flags(layout):
    plan, actor = layout
    job, _ = layouts.queue_layout_evaluation(plan, actor)
    due = timezone.now() + timedelta(minutes=5)
    OutboxMessage.objects.filter(pk=job.pk).update(attempts=2, available_after=due)
    same, _ = layouts.queue_layout_evaluation(plan, actor, retry=True, force=True)
    assert same.available_after == due and same.attempts == 2


@pytest.mark.parametrize("retry,force", [(False, False), (True, False), (False, True), (True, True)])
def test_cancelled_job_never_rearms_after_restore(layout, retry, force):
    plan, actor = layout
    job, operation = layouts.queue_layout_evaluation(plan, actor)
    Operation.objects.filter(pk=operation.pk).update(state="cancelled", error_code="FLOOR_PLAN_REMOVED_BY_USER")
    OutboxMessage.objects.filter(pk=job.pk).update(dead_lettered_at=timezone.now())
    same, stopped = layouts.queue_layout_evaluation(plan, actor, retry=retry, force=force)
    assert same.pk == job.pk and same.dead_lettered_at is not None
    assert stopped.state == "cancelled"
    assert OutboxMessage.objects.count() == 1


@pytest.mark.parametrize("state", ["removed_by_user", "replaced_by_user", "superseded_page_candidate"])
def test_stale_plan_instance_cannot_queue_hidden_record(layout, state):
    plan, actor = layout
    FloorPlan.objects.filter(pk=plan.pk).update(review_state=state)
    with pytest.raises(ValueError, match="^LAYOUT_REMOVED$"):
        layouts.queue_layout_evaluation(plan, actor, retry=True, force=True)
    assert not OutboxMessage.objects.exists()


def test_changed_checksum_replaces_only_this_plans_old_job(layout):
    plan, actor = layout
    other = sibling(plan)
    old, old_operation = layouts.queue_layout_evaluation(plan, actor)
    sibling_job, sibling_operation = layouts.queue_layout_evaluation(other, actor)
    FloorPlanAsset.objects.filter(pk=plan.asset_id).update(sha256="1" * 64)
    new, _ = layouts.queue_layout_evaluation(plan, actor)
    old.refresh_from_db()
    old_operation.refresh_from_db()
    sibling_job.refresh_from_db()
    sibling_operation.refresh_from_db()
    assert new.pk != old.pk and new.payload["assetSha256"] == "1" * 64
    assert old.dead_lettered_at is not None and old_operation.state == "cancelled"
    assert old_operation.error_code == "STALE_LAYOUT_ASSET"
    assert sibling_job.dead_lettered_at is None and sibling_operation.state == "pending"


@pytest.mark.parametrize("during_model", [False, True])
@pytest.mark.parametrize("change", ["checksum", "asset"])
def test_cached_payload_cannot_persist_after_asset_change(layout, inference, during_model, change):
    plan, actor = layout
    job, operation = layouts.queue_layout_evaluation(plan, actor)
    provider, factory, _, result = inference

    def replace():
        if change == "checksum":
            FloorPlanAsset.objects.filter(pk=plan.asset_id).update(sha256="2" * 64)
        else:
            asset = FloorPlanAsset.objects.get(pk=plan.asset_id)
            asset.pk = None
            asset.sha256 = "3" * 64
            asset.object_key += "-replacement"
            asset.save()
            FloorPlan.objects.filter(pk=plan.pk).update(asset=asset)

    if during_model:
        def analyze(*_args):
            assert not connection.in_atomic_block
            replace()
            return result
        provider.analyze_layout.side_effect = analyze
    else:
        replace()
    assert workers.process_outbox_message(job.pk) == "failed"
    operation.refresh_from_db()
    plan.refresh_from_db()
    job.refresh_from_db()
    assert operation.state == "cancelled" and operation.error_code == "STALE_LAYOUT_ASSET"
    assert job.dead_lettered_at is not None and job.attempts == 1
    assert plan.dimensions == [] and "cropEvaluation" not in plan.raw_extraction
    assert plan.raw_extraction["extractionStale"] is True and plan.raw_extraction["cropRevision"] == 4
    if not during_model:
        factory.assert_not_called()


def test_replacement_queue_during_bad_model_result_cannot_mark_new_crop_bad(layout, inference):
    plan, actor = layout
    old, operation = layouts.queue_layout_evaluation(plan, actor)
    provider, _, agent, result = inference
    agent._reconcile.return_value = []

    def analyze(*_args):
        FloorPlanAsset.objects.filter(pk=plan.asset_id).update(sha256="4" * 64)
        replacement, _ = layouts.queue_layout_evaluation(plan, actor)
        assert replacement.pk != old.pk
        return result

    provider.analyze_layout.side_effect = analyze
    workers.process_outbox_message(old.pk)
    plan.refresh_from_db()
    operation.refresh_from_db()
    assert operation.state == "cancelled"
    assert plan.review_state == "manual_crop_needs_evaluation"
    assert "qualityWarning" not in plan.raw_extraction
    assert OutboxMessage.objects.filter(dead_lettered_at__isnull=True).count() == 1


@pytest.mark.parametrize("during_model", [False, True])
def test_cancellation_wins_before_model_and_before_persistence(layout, inference, during_model):
    plan, actor = layout
    job, operation = layouts.queue_layout_evaluation(plan, actor)
    provider, factory, _, result = inference

    def cancel():
        Operation.objects.filter(pk=operation.pk).update(state="cancelled", error_code="FLOOR_PLAN_REMOVED_BY_USER")

    if during_model:
        def analyze(*_args):
            cancel()
            return result
        provider.analyze_layout.side_effect = analyze
    else:
        cancel()
    assert workers.process_outbox_message(job.pk) == "not_ready"
    plan.refresh_from_db()
    operation.refresh_from_db()
    assert operation.state == "cancelled" and plan.dimensions == []
    assert not InboxMessage.objects.filter(message_id=job.pk).exists()
    if not during_model:
        factory.assert_not_called()


def test_bad_boundary_is_terminal_and_spends_only_one_attempt(layout, inference):
    plan, actor = layout
    job, operation = layouts.queue_layout_evaluation(plan, actor)
    provider, _, agent, _ = inference
    agent._reconcile.return_value = []
    assert workers.process_pending_outbox(message_ids=[job.pk]) == {"processed": 0, "retry": 0, "failed": 1}
    job.refresh_from_db()
    operation.refresh_from_db()
    assert job.attempts == 1 and job.dead_lettered_at is not None and job.available_after is None
    assert job.last_error_code == operation.error_code == "MANUAL_CROP_NOT_SINGLE_LAYOUT"
    assert operation.state == "failed" and operation.result["failureCategory"] == "BAD_BOUNDARY"
    assert operation.result["retryable"] is False
    assert workers.process_outbox_message(job.pk) == "not_ready"
    provider.analyze_layout.assert_called_once()


@pytest.mark.parametrize("error,code", [
    (RuntimeError("provider offline"), "RuntimeError"),
    (ProviderUnavailable("private endpoint details"), "PROVIDER_UNAVAILABLE"),
    (ValueError("MANUAL_CROP_NOT_SINGLE_LAYOUT: untrusted extra text"), "ValueError"),
])
def test_unknown_and_provider_errors_back_off_with_exactly_one_increment(layout, inference, error, code):
    plan, actor = layout
    job, operation = layouts.queue_layout_evaluation(plan, actor)
    provider = inference[0]
    provider.analyze_layout.side_effect = error
    for attempt in (1, 2):
        assert workers.process_pending_outbox(message_ids=[job.pk])["failed"] == 1
        job.refresh_from_db()
        operation.refresh_from_db()
        assert job.attempts == attempt and job.dead_lettered_at is None
        assert job.last_error_code == operation.error_code == code
        assert job.available_after > timezone.now() and operation.result["retryable"] is True
        assert workers.process_outbox_message(job.pk) == "not_ready"
        OutboxMessage.objects.filter(pk=job.pk).update(available_after=None)
    assert provider.analyze_layout.call_count == 2


def test_unrelated_project_and_unauthorized_actor_cannot_queue(layout, django_user_model):
    plan, actor = layout
    unrelated = Project.objects.create(organization=plan.project.organization, submitted_name="Unrelated")
    with pytest.raises(Http404):
        queue_single_layout(unrelated, plan.pk, actor)
    unauthorized = django_user_model.objects.create_user(username="no-access", is_staff=True)
    with pytest.raises(PermissionDenied):
        queue_single_layout(plan.project, plan.pk, unauthorized)
    assert not OutboxMessage.objects.exists()


def test_canonical_project_can_queue_its_merged_family_layout(layout):
    plan, actor = layout
    canonical = Project.objects.create(organization=plan.project.organization, submitted_name="Canonical")
    ProjectMerge.objects.create(source_project=plan.project, target_project=canonical, state="applied", reason_code="TEST")
    Project.objects.filter(pk=plan.project_id).update(status="merged")
    assert queue_single_layout(canonical, plan.pk, actor)["status"] == "queued"


def test_sqlite_busy_retry_restarts_only_own_short_transaction(layout, monkeypatch):
    if connection.vendor != "sqlite":
        pytest.skip("SQLite writer reservation regression")
    plan, actor = layout
    real_queue = layouts._queue_layout_evaluation
    queue = Mock(side_effect=[OperationalError("database is locked"), ("message", "operation")])
    monkeypatch.setattr(layouts, "_queue_layout_evaluation", queue)
    sleep = Mock()
    monkeypatch.setattr(layouts.time, "sleep", sleep)
    assert layouts.queue_layout_evaluation(plan, actor) == ("message", "operation")
    assert queue.call_count == 2
    sleep.assert_called_once()
    queue.reset_mock()
    queue.side_effect = OperationalError("database is locked")
    with transaction.atomic(), pytest.raises(OperationalError):
        layouts.queue_layout_evaluation(plan, actor)
    queue.assert_called_once()
    monkeypatch.setattr(layouts, "_queue_layout_evaluation", real_queue)


def test_evaluator_rejects_outer_transaction_before_model_or_status_write(layout, inference):
    plan, actor = layout
    job, operation = layouts.queue_layout_evaluation(plan, actor)
    with transaction.atomic(), pytest.raises(RuntimeError, match="outside database transactions"):
        layouts.evaluate_layout_crop(job.payload)
    operation.refresh_from_db()
    assert operation.state == "pending"
    inference[1].assert_not_called()


def test_terminal_failure_survives_crash_before_outbox_dead_letter(layout, inference):
    plan, actor = layout
    job, operation = layouts.queue_layout_evaluation(plan, actor)
    Operation.objects.filter(pk=operation.pk).update(state="failed", error_code="MANUAL_CROP_NOT_SINGLE_LAYOUT")
    assert workers.process_outbox_message(job.pk) == "not_ready"
    job.refresh_from_db()
    assert job.dead_lettered_at is not None and job.attempts == 0
    inference[1].assert_not_called()


def test_completed_old_checksum_is_not_shown_as_current_extraction(layout):
    plan, actor = layout
    _, operation = layouts.queue_layout_evaluation(plan, actor)
    Operation.objects.filter(pk=operation.pk).update(state="completed")
    FloorPlanAsset.objects.filter(pk=plan.asset_id).update(sha256="5" * 64)
    plan.dimensions = [{"room": "old data"}]
    assert layout_evaluation_states([plan])[str(plan.pk)]["state"] == "not_queued"


def test_explicit_bad_boundary_retry_clears_failure_metadata_but_does_not_execute(layout, inference):
    plan, actor = layout
    job, operation = layouts.queue_layout_evaluation(plan, actor)
    inference[2]._reconcile.return_value = []
    assert workers.process_outbox_message(job.pk) == "failed"
    response = queue_single_layout(plan.project, plan.pk, actor, retry=True)
    operation.refresh_from_db()
    job.refresh_from_db()
    assert response["status"] == "queued" and response["messageId"] == str(job.pk)
    assert operation.error_code == "" and "failureCategory" not in operation.result
    assert job.attempts == 0 and job.dead_lettered_at is None
    inference[0].analyze_layout.assert_called_once()


def test_provider_exception_cannot_overwrite_cancellation(layout, inference):
    plan, actor = layout
    job, operation = layouts.queue_layout_evaluation(plan, actor)

    def cancel_and_fail(*_args):
        Operation.objects.filter(pk=operation.pk).update(state="cancelled", error_code="FLOOR_PLAN_REMOVED_BY_USER")
        raise RuntimeError("provider failed after cancellation")

    inference[0].analyze_layout.side_effect = cancel_and_fail
    assert workers.process_outbox_message(job.pk) == "failed"
    operation.refresh_from_db()
    job.refresh_from_db()
    assert operation.state == "cancelled" and operation.error_code == "FLOOR_PLAN_REMOVED_BY_USER"
    assert job.dead_lettered_at is not None and job.available_after is None


def test_worker_error_bookkeeping_does_not_undo_new_explicit_retry(layout, inference, monkeypatch):
    plan, actor = layout
    job, _ = layouts.queue_layout_evaluation(plan, actor)
    inference[0].analyze_layout.side_effect = RuntimeError("offline")
    record = workers._record_outbox_failure

    def retry_before_bookkeeping(message_id, exc, **kwargs):
        layouts.queue_layout_evaluation(plan, actor, retry=True)
        return record(message_id, exc, **kwargs)

    monkeypatch.setattr(workers, "_record_outbox_failure", retry_before_bookkeeping)
    assert workers.process_outbox_message(job.pk) == "failed"
    job.refresh_from_db()
    operation = Operation.objects.get(pk=job.payload["operationId"])
    assert job.attempts == 0 and job.available_after is None and job.dead_lettered_at is None
    assert operation.state == "pending" and operation.error_code == ""
