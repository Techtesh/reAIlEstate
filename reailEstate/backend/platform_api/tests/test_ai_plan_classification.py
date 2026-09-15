from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from platform_api.floor_plan_agent import FloorPlanAgent, FloorPlanCandidate
from platform_api.floor_plan_vision import OpenAIFloorPlanVisionProvider
from platform_api.integrations import InvalidProviderResponse
from platform_api.models import FloorPlan


def classify(result):
    provider = object.__new__(OpenAIFloorPlanVisionProvider)
    provider.analyze = Mock(return_value=result)
    return provider.analyze_classified_plan(b"image", "image/png", FloorPlanAgent._vision_schema())


@pytest.mark.parametrize("kind", ["facade", "elevation", "notation_only", "non_plan", "uncertain"])
def test_rejected_image_has_no_plans(kind):
    result = classify({"pageType": kind, "visualEvidence": "No horizontal room boundaries", "floorPlans": []})
    assert result["floorPlans"] == []


def test_nonplan_with_fabricated_plan_is_rejected():
    with pytest.raises(InvalidProviderResponse):
        classify({"pageType": "facade", "visualEvidence": "Exterior building", "floorPlans": [{"planScope": "floor"}]})


def test_uncertain_scope_is_rejected():
    with pytest.raises(InvalidProviderResponse):
        classify({"pageType": "floor_plan", "visualEvidence": "Rooms", "floorPlans": [{"planScope": "unknown"}]})


def test_real_plan_with_notes_is_allowed():
    result = classify({"pageType": "floor_plan", "visualEvidence": "Horizontal rooms and stairs, with a notes table", "floorPlans": [{"planScope": "floor"}]})
    assert len(result["floorPlans"]) == 1


def test_floor_page_with_unit_labels_persists_as_one_floor_candidate():
    agent = FloorPlanAgent(SimpleNamespace(code="azure_foundry", model="gpt-4.1", profile_id="cloud"))
    result = {
        "pageType": "floor_plan", "visualEvidence": "One floor with a shared corridor and several units",
        "floorPlans": [
            {"planName": "Unit 1", "planScope": "flat", "configurationType": "1 BHK",
             "carpetAreaSqFt": 430, "orientation": None, "dimensions": [], "features": [], "confidence": 0.95},
            {"planName": "Unit 2", "planScope": "flat", "configurationType": "2 BHK",
             "carpetAreaSqFt": 628, "orientation": None, "dimensions": [], "features": [], "confidence": 0.95},
        ],
    }
    candidates = agent._vision_candidates(4, result, SimpleNamespace())
    assert len(candidates) == 1
    assert candidates[0].plan_scope == "floor"
    assert len(candidates[0].features["identifiedUnits"]) == 2


def test_ai_persistence_uses_unique_run_name_and_requested_state(monkeypatch):
    captured = []
    def save(**kwargs):
        captured.append(deepcopy(kwargs))
        return SimpleNamespace(id=uuid4()), True
    monkeypatch.setattr(FloorPlan.objects, "update_or_create", save)
    agent = FloorPlanAgent(SimpleNamespace(code="azure_foundry", model="gpt-4.1", profile_id="cloud"))
    monkeypatch.setattr(agent, "_create_fact_candidates", lambda plan: None)
    candidate = FloorPlanCandidate(1, "Floor plan page 1", "", None, [], 0.9, "Rooms", plan_scope="floor")
    for _ in range(2):
        agent._persist(SimpleNamespace(id=uuid4(), project="project", document="document"), [candidate])
    assert captured[0]["plan_name"] != captured[1]["plan_name"]
    assert all(item["plan_name"] != candidate.plan_name for item in captured)
    assert all(item["defaults"]["review_state"] == "gen_with_ai_needs_review" for item in captured)