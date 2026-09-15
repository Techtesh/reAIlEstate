import hashlib
from datetime import timedelta
from uuid import uuid4

import pymupdf
import pytest
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.urls import resolve, reverse
from django.utils import timezone

from platform_api.artifact_storage import storage
from platform_api.models import EvidenceDocument, FloorPlanAsset, Organization, OutboxMessage, Project
from platform_api.source_page_additions import PAGE_EVENT, add_source_pages


def test_crop_urls_do_not_hit_admin_change_catch_all():
    plan_id = uuid4()
    url = reverse("admin:platform_api_floorplan_manual_crops", args=[plan_id])
    assert resolve(url).url_name == "platform_api_floorplan_manual_crops"
    assert resolve(url + "change/").url_name == "platform_api_floorplan_manual_crops_legacy"
    assert resolve(url + "change/").kwargs["object_id"] == str(plan_id)


@pytest.fixture
def source(tmp_path, settings, monkeypatch):
    settings.WORKSPACE_DATA_ROOT = tmp_path
    settings.MEDIA_ROOT = tmp_path / "media"
    from platform_api import source_page_additions
    monkeypatch.setattr(source_page_additions, "resolve_ai_vision_selection", lambda _: {"profileId": "test"})
    actor = get_user_model().objects.create_superuser(username="page-reviewer", password="test-only")
    org = Organization.objects.create(legal_name="Page sources")
    project = Project.objects.create(organization=org, submitted_name="Page picker")
    with pymupdf.open() as pdf:
        for index in range(3):
            pdf.new_page().insert_text((40, 40), f"Source page {index + 1}")
        data = pdf.tobytes()
    key = storage.save(f"output/media/projects/{project.pk}/source.pdf", ContentFile(data))
    document = EvidenceDocument.objects.create(
        project=project, original_name="source.pdf", declared_media_type="application/pdf",
        object_key=key, byte_length=len(data), sha256=hashlib.sha256(data).hexdigest(),
        scan_status="clean", status="needs_review", uploaded_by=actor,
        retention_until=timezone.now() + timedelta(days=30),
    )
    return project, document, actor, data


@pytest.mark.django_db
def test_selected_layout_pages_enter_cart_without_whole_document_reprocessing(source):
    project, document, actor, original = source
    result = add_source_pages(project, document.pk, [3, 1], "layouts", actor)
    assert [row["page"] for row in result] == [1, 3]
    assert OutboxMessage.objects.filter(event_type=PAGE_EVENT).count() == 2
    assert not OutboxMessage.objects.filter(event_type="document.processing.requested").exists()
    assert set(FloorPlanAsset.objects.values_list("page_number", flat=True)) == {1, 3}
    with storage.open(document.object_key, "rb") as stream:
        assert stream.read() == original
    add_source_pages(project, document.pk, [3, 1], "layouts", actor)
    assert OutboxMessage.objects.count() == 2


@pytest.mark.django_db
def test_amenity_pages_are_queued_for_extraction_and_review(source):
    project, document, actor, _ = source
    add_source_pages(project, document.pk, [2], "amenities", actor)
    assert FloorPlanAsset.objects.get().source_bounds["category"] == "amenities"
    message = OutboxMessage.objects.get(event_type=PAGE_EVENT)
    assert message.payload["category"] == "amenities"
    assert "floorPlanId" not in message.payload
    assert project.review_tasks.filter(task_type="source_page", state="open").exists()


@pytest.mark.django_db
def test_same_plan_page_added_through_both_tabs_reuses_asset_and_job(source):
    project, document, actor, _ = source
    first = add_source_pages(project, document.pk, [2], "floor-plans", actor)
    second = add_source_pages(project, document.pk, [2], "layouts", actor)
    assert second[0]["existing"] is True
    assert first[0]["assetId"] == second[0]["assetId"]
    assert FloorPlanAsset.objects.count() == OutboxMessage.objects.count() == 1


@pytest.mark.django_db
def test_recovery_queues_legacy_amenity_asset_without_duplicate_images(source):
    from platform_api.source_page_additions import queue_saved_amenity_pages
    from platform_api.models import Operation
    project, document, actor, _ = source
    add_source_pages(project, document.pk, [2], "amenities", actor)
    # Simulate pre-fix pages which were saved without operations or outbox jobs.
    OutboxMessage.objects.all().delete()
    Operation.objects.all().delete()
    queue_saved_amenity_pages(project, actor)
    queue_saved_amenity_pages(project, actor)
    assert FloorPlanAsset.objects.count() == OutboxMessage.objects.count() == 1
    assert not OutboxMessage.objects.filter(event_type="document.processing.requested").exists()


@pytest.mark.django_db
def test_amenity_worker_persists_evidence_preserves_previous_claims_and_replays_once(source, monkeypatch):
    from unittest.mock import Mock
    from platform_api import floor_plan_vision
    from platform_api.models import FactCandidate, Operation
    from platform_api.source_page_additions import evaluate_source_page
    project, document, actor, _ = source
    provider = Mock(code="test", model="test-model")
    provider.analyze_project_page.return_value = {
        "facts": [{"fieldCode": "amenities_social", "value": "swimming_pool",
                   "evidenceText": "Swimming pool"}], "towers": [],
    }
    monkeypatch.setattr(floor_plan_vision, "get_floor_plan_vision_provider", lambda _: provider)
    add_source_pages(project, document.pk, [1, 2], "amenities", actor)
    jobs = list(OutboxMessage.objects.order_by("occurred_at"))
    for job in jobs:
        evaluate_source_page(job.payload)
    evaluate_source_page(jobs[0].payload)
    assert provider.analyze_project_page.call_count == 2
    assert FactCandidate.objects.filter(field_code="amenities", review_state="unreviewed").count() == 2
    claim = project.project_amenities.get(amenity__taxonomy_code="swimming_pool")
    assert {item["pageNumber"] for item in claim.evidence_summary["extracted"]} == {1, 2}
    assert Operation.objects.filter(state="completed").count() == 2
    assert not project.floor_plans.exists()
    assert not project.ingestion_runs.filter(summary__has_key="projectOutput").exists()


@pytest.mark.django_db
def test_amenity_failure_remains_retryable_not_completed(source, monkeypatch):
    from unittest.mock import Mock
    from platform_api import floor_plan_vision
    from platform_api.models import Operation
    from platform_api.source_page_additions import evaluate_source_page
    project, document, actor, _ = source
    provider = Mock()
    provider.analyze_project_page.side_effect = ValueError("Invalid result")
    monkeypatch.setattr(floor_plan_vision, "get_floor_plan_vision_provider", lambda _: provider)
    add_source_pages(project, document.pk, [2], "amenities", actor)
    with pytest.raises(ValueError):
        evaluate_source_page(OutboxMessage.objects.get().payload)
    assert Operation.objects.get().state == "failed"
    assert not project.project_amenities.exists()
    provider.close.assert_called_once()


@pytest.mark.django_db
def test_invalid_page_has_no_side_effects(source):
    project, document, actor, _ = source
    with pytest.raises(ValueError):
        add_source_pages(project, document.pk, [999], "layouts", actor)
    assert not FloorPlanAsset.objects.exists()
    assert not OutboxMessage.objects.exists()


@pytest.mark.django_db
def test_mark_layout_also_queues_extraction(source):
    from platform_api.models import DocumentLayoutPage
    project, document, actor, _ = source
    add_source_pages(project, document.pk, [2], "layouts", actor, mark_layout=True, notes="Missed page")
    decision = DocumentLayoutPage.objects.get(document=document, page_number=2)
    assert decision.notes == "Missed page"
    assert decision.state == "layout"
    message = OutboxMessage.objects.get(event_type=PAGE_EVENT)
    assert message.payload["documentId"] == str(document.pk)
    assert FloorPlanAsset.objects.get(pk=message.payload["assetId"]).page_number == 2
    add_source_pages(project, document.pk, [2], "layouts", actor, mark_layout=True)
    assert OutboxMessage.objects.count() == 1


@pytest.mark.django_db
def test_mark_failure_does_not_save_unqueued_decision(source, monkeypatch):
    from platform_api.models import DocumentLayoutPage
    from platform_api import source_page_additions
    project, document, actor, _ = source
    def unavailable(_):
        raise ValueError("No configured profile")
    monkeypatch.setattr(source_page_additions, "resolve_ai_vision_selection", unavailable)
    with pytest.raises(ValueError):
        add_source_pages(project, document.pk, [2], "layouts", actor, mark_layout=True)
    assert not DocumentLayoutPage.objects.exists()
    assert not OutboxMessage.objects.exists()


@pytest.mark.django_db
def test_mark_layout_api_returns_pending_queue(source):
    from rest_framework.test import APIRequestFactory, force_authenticate
    from platform_api.views import MarkDocumentLayoutPageView
    project, document, actor, _ = source
    request = APIRequestFactory().post("/", {"pageNumber": 2}, format="json")
    force_authenticate(request, user=actor)
    response = MarkDocumentLayoutPageView.as_view()(request, project_id=project.pk, document_id=document.pk)
    assert response.status_code == 202
    assert response.data["queueStatus"] == "pending"
    assert OutboxMessage.objects.filter(event_type=PAGE_EVENT).count() == 1