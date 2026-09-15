"""Offline coverage for the script/AI switch and deterministic PDF identification."""

import hashlib
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pymupdf
import pytest
from django.utils import timezone

from platform_api.floor_plan_agent import FloorPlanAgent
from platform_api.floor_plan_pages import FloorPlanPageSelector
from platform_api.floor_plan_rules import RuleBasedPlanProvider, classify_plan_scope, rule_selection
from platform_api import floor_plan_vision as vision
from platform_api.models import EvidenceDocument, Organization, Project, ReviewTask
from platform_api.rag import ingest_document
from platform_api.workers import enqueue_document


@pytest.fixture
def configuration(settings):
    settings.FLOOR_PLAN_EXTRACTION_MODE = "script"
    settings.FLOOR_PLAN_VISION_PROFILES = {"local": {"provider": "ollama", "model": "vision-model"}}
    settings.FLOOR_PLAN_DEFAULT_VISION_PROFILE = "local"


def test_script_mode_ignores_ai_configuration(configuration, settings):
    settings.FLOOR_PLAN_VISION_PROFILES = "invalid AI config is irrelevant to scripts"
    with patch.object(vision, "_configured_profiles", side_effect=AssertionError("AI config accessed")):
        assert vision.resolve_vision_selection() == rule_selection()
        assert isinstance(vision.get_floor_plan_vision_provider(), RuleBasedPlanProvider)
        assert vision.list_floor_plan_vision_profiles()["defaultProfileId"] == "rules-only"


def test_script_mode_rejects_new_ai_requests(configuration):
    with pytest.raises(ValueError):
        vision.resolve_vision_selection("local")


def test_mode_switch_preserves_existing_selections(configuration, settings):
    script_selection = vision.resolve_vision_selection()
    settings.FLOOR_PLAN_EXTRACTION_MODE = "ai"
    ai_selection = vision.resolve_vision_selection()
    assert ai_selection["provider"] == "ollama"
    assert vision.list_floor_plan_vision_profiles()["defaultProfileId"] == "local"
    assert isinstance(vision.get_floor_plan_vision_provider(script_selection), RuleBasedPlanProvider)
    settings.FLOOR_PLAN_EXTRACTION_MODE = "script"
    assert vision.get_floor_plan_vision_provider(ai_selection).model == "vision-model"
    assert vision.get_floor_plan_vision_provider().code == "rules"


def test_invalid_mode_fails_closed(configuration, settings):
    settings.FLOOR_PLAN_EXTRACTION_MODE = "typo"
    with pytest.raises(ValueError):
        vision.resolve_vision_selection()


@pytest.mark.parametrize("text,expected", [
    ("Typical floor plan", "floor"), ("2 BHK flat layout", "flat"),
    ("Apartment plan", "flat"), ("2BHK Kitchen Living", "flat"),
    ("Floor plan and unit plan", "unknown"), ("Site layout", "unknown"), ("", "unknown"),
])
def test_scope_rules(text, expected):
    assert classify_plan_scope(text) == expected


def test_bhk_marketing_page_is_not_a_plan():
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Luxury 3 BHK residences. Carpet area: 1200 sqft")
        content = pdf.tobytes()
    assert FloorPlanPageSelector().select(content, "application/pdf") == []


def test_layout_pages_win_when_plan_budget_is_limited():
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Typical floor plan Tower A")
        pdf.new_page().insert_text(
            (72, 72), "2 BHK unit plan Living Bedroom Kitchen RERA carpet area 720 sqft"
        )
        content = pdf.tobytes()
    selected = FloorPlanPageSelector(maximum_pages=1).select(content, "application/pdf")
    assert [item.page_number for item in selected] == [2]
    assert "individual_layout" in selected[0].reasons


@pytest.mark.django_db
def test_script_pipeline_stores_pages_without_vision(configuration, settings, tmp_path, django_user_model):
    settings.MEDIA_ROOT = tmp_path
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Typical floor plan")
        pdf.new_page().insert_text((72, 72), "2 BHK unit layout. RERA carpet area: 720 sqft")
        content = pdf.tobytes()
    user = django_user_model.objects.create_user(username="script-builder")
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Builder"), submitted_name="Test")
    document = EvidenceDocument.objects.create(
        project=project, uploaded_by=user, object_key="quarantine/script.pdf", original_name="script.pdf",
        declared_media_type="application/pdf", byte_length=len(content), sha256=hashlib.sha256(content).hexdigest(),
        retention_until=timezone.now() + timedelta(days=7), scan_status="clean",
    )
    message = enqueue_document(document, uuid4())
    assert message.payload["visionSelection"] == rule_selection()
    run = ingest_document(document, content)
    with patch.object(vision.OpenAIFloorPlanVisionProvider, "analyze", side_effect=AssertionError("AI called")):
        workflow = FloorPlanAgent().run(run, content, "application/pdf")
    assert workflow.status == "completed"
    assert workflow.result["extractionMode"] == "script"
    assert workflow.floor_plan_assets.count() == 2
    plans = list(workflow.floor_plans.order_by("page_number"))
    assert [plan.plan_scope for plan in plans] == ["floor", "flat"]
    assert plans[1].configuration_type == "2BHK"
    assert plans[1].carpet_area_sqft == 720
    assert all(plan.review_state == "needs_review" for plan in plans)
    assert not ReviewTask.objects.filter(task_type="floor_plan_vision").exists()