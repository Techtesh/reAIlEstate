"""A repeated extraction must not resurrect hidden records or review tasks."""

from unittest.mock import Mock

import pytest

from platform_api.floor_plan_agent import FloorPlanAgent, FloorPlanCandidate
from platform_api.models import FactCandidate, FloorPlan, ReviewTask
from platform_api.unit_segmentation import replace_unit_segments

from .test_floor_plan_layout_jobs import source  # Shared isolated image/database fixture.


pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("state", ["removed_by_user", "replaced_by_user", "superseded_page_candidate"])
@pytest.mark.parametrize("mode", ["ai", "script"])
def test_persist_keeps_hidden_upsert_target_unchanged(source, monkeypatch, state, mode):
    plan, _ = source
    agent = FloorPlanAgent(Mock())
    monkeypatch.setattr(agent, "_provider_metadata", lambda: {"extractionMode": mode})
    name = "Known layout"
    plan.plan_name = f"{name} [AI {plan.workflow_run_id}]" if mode == "ai" else name
    plan.review_state = state
    plan.save()
    before = FloorPlan.objects.filter(pk=plan.pk).values().get()
    candidate = FloorPlanCandidate(
        page_number=1, plan_name=name, configuration_type="2BHK", carpet_area_sqft=None,
        dimensions=[], confidence=0.9, evidence_text="Repeated extraction", asset=plan.asset,
    )
    plans = agent._persist(plan.workflow_run, [candidate])
    agent._review_gate(plan.workflow_run, plans)
    assert plans == []
    assert FloorPlan.objects.filter(pk=plan.pk).values().get() == before
    assert not ReviewTask.objects.exists() and not FactCandidate.objects.exists()


@pytest.mark.parametrize("state", ["removed_by_user", "replaced_by_user", "superseded_page_candidate"])
def test_vision_placeholder_keeps_hidden_upsert_target_unchanged(source, monkeypatch, state):
    plan, _ = source
    agent = FloorPlanAgent(Mock())
    monkeypatch.setattr(agent, "_provider_metadata", lambda: {"extractionMode": "ai"})
    plan.plan_name = "Floor plan page 1"
    plan.review_state = state
    plan.save()
    before = FloorPlan.objects.filter(pk=plan.pk).values().get()
    assert agent._persist_vision_placeholders(plan.workflow_run) == []
    assert FloorPlan.objects.filter(pk=plan.pk).values().get() == before
    assert not ReviewTask.objects.exists()


@pytest.mark.parametrize("stale_instance", [False, True])
@pytest.mark.parametrize("state", ["removed_by_user", "replaced_by_user", "superseded_page_candidate"])
def test_segmentation_guard_reloads_hidden_source_before_any_replacement(source, state, stale_instance):
    plan, actor = source
    FloorPlan.objects.filter(pk=plan.pk).update(review_state=state)
    if not stale_instance:
        plan.refresh_from_db()
    with pytest.raises(ValueError, match="FLOOR_PLAN_SOURCE_HIDDEN"):
        replace_unit_segments(plan, [], "ai", actor=actor)
    assert FloorPlan.objects.count() == 1
    assert not ReviewTask.objects.exists()