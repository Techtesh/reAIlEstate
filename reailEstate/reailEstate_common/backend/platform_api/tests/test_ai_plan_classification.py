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


def layout_result(kind="flat_layout", plans=None):
    return {"pageType": kind, "visualEvidence": "One apartment with enclosed rooms",
            "floorPlans": plans if plans is not None else [{
                "planName": "Unit 03", "planScope": "flat", "configurationType": "1BHK",
                "carpetAreaSqFt": None, "orientation": None, "dimensions": [],
                "features": [], "confidence": 0.9,
            }]}


def test_individual_layout_uses_its_own_schema_and_stays_flat():
    from jsonschema import validate
    provider = object.__new__(OpenAIFloorPlanVisionProvider)
    result = layout_result()
    def analyze(image, media_type, schema, task):
        validate(result, schema)
        assert schema["properties"]["floorPlans"]["maxItems"] == 1
        assert schema["properties"]["floorPlans"]["items"]["properties"]["planScope"]["enum"] == ["flat"]
        assert "one apartment is flat_layout" in task
        return result
    provider.analyze = Mock(side_effect=analyze)
    output = provider.analyze_layout(b"image", "image/png", FloorPlanAgent._vision_schema())
    candidates = FloorPlanAgent(provider)._vision_candidates(1, output)
    assert len(candidates) == 1 and candidates[0].plan_scope == "flat"


def test_native_measurement_schema_requires_unit_evidence_and_rejects_feet_only_response():
    from jsonschema import validate, ValidationError
    from .test_room_dimensions import native_room
    schema = FloorPlanAgent._vision_schema()
    result = layout_result()
    result["floorPlans"][0]["dimensions"] = [native_room("Living", 7, 3.87, "m")]
    validate({"floorPlans": result["floorPlans"]}, schema)
    result["floorPlans"][0]["dimensions"] = [{"room": "Living", "lengthFt": 7, "widthFt": 3.87}]
    with pytest.raises(ValidationError):
        validate({"floorPlans": result["floorPlans"]}, schema)
    room = native_room("Living", 7, 3.87, "m")
    del room["measurements"][0]["unitSource"]
    result["floorPlans"][0]["dimensions"] = [room]
    with pytest.raises(ValidationError):
        validate({"floorPlans": result["floorPlans"]}, schema)


@pytest.mark.parametrize("method", ["analyze_layout", "analyze_classified_plan"])
def test_all_layout_prompts_include_unlabeled_foyers_passages_and_feet_inches(method):
    provider = object.__new__(OpenAIFloorPlanVisionProvider)
    provider.analyze = Mock(return_value=layout_result())
    getattr(provider, method)(b"image", "image/png", FloorPlanAgent._vision_schema())
    prompt = provider.analyze.call_args.kwargs["task"]
    assert "entry foyers and internal passages/corridors" in prompt
    assert "no printed room name" in prompt
    assert "feet + inches/12" in prompt
    assert "never treat inches as a decimal suffix" in prompt
    assert "Prefer printed feet/inches" in prompt
    assert "Capture BOTH metric and imperial pairs" in prompt
    assert "never assume decimals mean feet" in prompt
    assert "lengthEstimated/widthEstimated true" in prompt
    assert "Never disguise estimates as printed measurements" in prompt


@pytest.mark.parametrize("kind", ["full_floor", "multiple_layouts", "non_plan", "uncertain"])
def test_layout_evaluator_does_not_extract_from_whole_floors_or_nonplans(kind):
    provider = object.__new__(OpenAIFloorPlanVisionProvider)
    provider.analyze = Mock(return_value=layout_result(kind, []))
    assert provider.analyze_layout(b"image", "image/png", FloorPlanAgent._vision_schema())["floorPlans"] == []
    provider.analyze.return_value = layout_result(kind)
    with pytest.raises(InvalidProviderResponse):
        provider.analyze_layout(b"image", "image/png", FloorPlanAgent._vision_schema())


@pytest.mark.parametrize("invalid", [
    {"floorPlans": []}, {"visualEvidence": ""},
    {"floorPlans": [{"planScope": "floor"}]},
    {"floorPlans": [{"planScope": "flat"}, {"planScope": "flat"}]},
])
def test_layout_evaluator_rejects_missing_evidence_wrong_scope_or_multiple_units(invalid):
    provider = object.__new__(OpenAIFloorPlanVisionProvider)
    provider.analyze = Mock(return_value=layout_result() | invalid)
    with pytest.raises(InvalidProviderResponse):
        provider.analyze_layout(b"image", "image/png", FloorPlanAgent._vision_schema())


@pytest.mark.parametrize("kind", ["multi_unit_floor_plan", "layout_sheet", "single_unit_layout"])
def test_full_floor_evaluation_returns_regions_and_floor_counts_not_room_dimensions(kind):
    from jsonschema import validate
    provider = object.__new__(OpenAIFloorPlanVisionProvider)
    result = {"pageType": kind, "unitCount": 1, "liftCount": 2, "staircaseCount": None,
              "refugeArea": {"presence": "unknown", "area": None, "unit": None, "evidence": "No visible refuge label"},
              "commonAreas": [],
              "visualEvidence": "One visible unit and two lift symbols",
              "regions": [{"label": "Unit 03", "x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8,
                           "confidence": 0.9, "evidence": "Unit entrance", "floors": ["FIRST FLOOR"]}]}
    def analyze(image, media_type, schema, task):
        validate(result, schema)
        assert "floorPlans" not in schema["properties"]
        assert "not individual room measurements" in task
        return result
    provider.analyze = Mock(side_effect=analyze)
    regions, metadata = provider.analyze_full_floor(b"image", "image/png")
    assert len(regions) == metadata["unitCount"] == 1
    assert metadata["flatsPerFloor"] == (1 if kind == "multi_unit_floor_plan" else None)
    assert metadata["liftCount"] == 2 and metadata["staircaseCount"] is None


def test_unit_regions_compatibility_uses_full_floor_evaluator():
    provider = object.__new__(OpenAIFloorPlanVisionProvider)
    provider.analyze_full_floor = Mock(return_value=([], {}))
    assert provider.analyze_unit_regions(b"image", "image/png", 2) == ([], {})
    provider.analyze_full_floor.assert_called_once_with(b"image", "image/png", 2)


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


@pytest.mark.django_db
def test_ai_persistence_uses_unique_run_name_and_requested_state(monkeypatch):
    captured = []
    def save(**kwargs):
        captured.append(deepcopy(kwargs))
        return SimpleNamespace(id=uuid4()), True
    monkeypatch.setattr(FloorPlan.objects, "update_or_create", save)
    from unittest.mock import Mock
    existing = Mock()
    existing.filter.return_value.first.return_value = None
    monkeypatch.setattr(FloorPlan.objects, "select_for_update", Mock(return_value=existing))
    agent = FloorPlanAgent(SimpleNamespace(code="azure_foundry", model="gpt-4.1", profile_id="cloud"))
    monkeypatch.setattr(agent, "_create_fact_candidates", lambda plan: None)
    candidate = FloorPlanCandidate(1, "Floor plan page 1", "", None, [], 0.9, "Rooms", plan_scope="floor")
    for _ in range(2):
        agent._persist(SimpleNamespace(id=uuid4(), project="project", document="document"), [candidate])
    assert captured[0]["plan_name"] != captured[1]["plan_name"]
    assert all(item["plan_name"] != candidate.plan_name for item in captured)
    assert all(item["defaults"]["review_state"] == "gen_with_ai_needs_review" for item in captured)
