"""Alias presentation and persistence regressions; no live model calls."""

import csv
import io
import json
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.contrib.auth.models import AnonymousUser, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.template.loader import render_to_string

from platform_api.artifact_storage import storage
from platform_api.floor_plan_agent import FloorPlanAgent, FloorPlanCandidate
from platform_api.layout_names import clean_layout_name, save_layout_name, validate_layout_display_name
from platform_api.models import AuditEvent, FloorPlan, Operation, Project, ProjectMerge
from platform_api.project_outputs import layout_metrics, store_outputs
from platform_api.renderers import CamelCaseJSONRenderer
from platform_api.serializers import FloorPlanSerializer

from .test_floor_plan_layout_jobs import source  # Isolated database/image fixture.


GENERATED_ID = "12345678-1234-4321-8765-123456789abc"


@pytest.mark.parametrize("suffix", [
    f" [AI {GENERATED_ID}]", f" [manual {GENERATED_ID}]",
    " [ai segment a0b1c2d3]", " [script segment A0B1C2D3]", " [manual segment a0b1c2d3]",
    f" [script segment a0b1c2d3] [AI {GENERATED_ID}]",
])
def test_only_known_trailing_generated_suffixes_are_removed(suffix):
    plan = FloorPlan(plan_name="2 BHK — East [Wing A]" + suffix)
    assert plan.display_title == "2 BHK — East [Wing A]"
    assert plan.plan_name.endswith(suffix)


@pytest.mark.parametrize("name", [
    "Unit 12", "2 BHK — East", "Tower A [East]", "AI concept", "Unit [AI concept]",
    "Unit [script segment east]", "Unit [ai segment abcdefghi]", "Unit [manual not-a-uuid]",
    "Unit  12", f"[AI {GENERATED_ID}] — East", f"Unit {GENERATED_ID}",
])
def test_meaningful_names_are_unchanged(name):
    assert clean_layout_name(name) == name


@pytest.mark.parametrize("name", ["", GENERATED_ID, GENERATED_ID.replace("-", ""),
    f"{{{GENERATED_ID}}}", f"{GENERATED_ID} [AI {GENERATED_ID}]", f"[manual {GENERATED_ID}]"])
@pytest.mark.parametrize("configuration,page,expected", [
    ("2BHK", 3, "2 BHK layout — page 3"), ("2.5 bhk", 7, "2.5 BHK layout — page 7"),
    ("", 2, "Layout — page 2"), ("unknown", 2, "Layout — page 2"),
    ("", None, "Floor plan"), ("2BHK", None, "2 BHK layout"),
])
def test_empty_and_uuid_only_names_have_human_fallback(name, configuration, page, expected):
    plan = FloorPlan(plan_name=name, configuration_type=configuration, page_number=page)
    assert plan.display_title == expected


def test_alias_wins_without_stripping_meaningful_alias_brackets():
    alias = f"Reviewer choice [AI {GENERATED_ID}]"
    plan = FloorPlan(display_name=alias, plan_name="Extracted name")
    assert plan.display_title == alias
    assert FloorPlan(plan_name="East\x00\u202eWing").display_title == "East  Wing"


@pytest.mark.parametrize("value", [None, 12, {}, [], "a" * 161, "\t", "Unit\n1", "\x00",
    "\x7f", "\x85", "\u202eUnit", "\u200b", "\ud800", "Unit\u2028A", "Unit\u2029A"])
def test_alias_input_rejects_length_controls_and_non_strings(value):
    with pytest.raises(ValidationError):
        validate_layout_display_name(value)


def test_alias_input_trims_and_accepts_limit_and_blank():
    assert validate_layout_display_name("  East home  ") == "East home"
    assert validate_layout_display_name("a" * 160) == "a" * 160
    assert validate_layout_display_name("   ") == ""


@pytest.mark.django_db
def test_save_and_clear_are_audited_and_preserve_identity_and_provenance(source):
    plan, actor = source
    before = FloorPlan.objects.filter(pk=plan.pk).values().get()
    saved = save_layout_name(plan.project, plan.pk, "  Garden home  ", actor)
    assert saved.display_title == "Garden home"
    after = FloorPlan.objects.filter(pk=plan.pk).values().get()
    assert {key: value for key, value in before.items() if key not in {"display_name", "updated_at"}} == {
        key: value for key, value in after.items() if key not in {"display_name", "updated_at"}
    }
    event = AuditEvent.objects.get(action="layout.display_name_changed")
    assert event.actor == actor and event.entity_id == plan.pk
    assert event.metadata == {"projectId": str(plan.project_id), "dashboardProjectId": str(plan.project_id),
                              "previousDisplayName": "", "displayName": "Garden home"}
    save_layout_name(plan.project, plan.pk, "Garden home", actor)
    assert AuditEvent.objects.filter(action="layout.display_name_changed").count() == 1
    cleared = save_layout_name(plan.project, plan.pk, "   ", actor)
    assert cleared.display_name == "" and cleared.display_title == "Full floor"
    assert AuditEvent.objects.filter(action="layout.display_name_changed", metadata__displayName="",
                                    metadata__previousDisplayName="Garden home").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("active,staff,permission", [(False, True, True), (True, False, True), (True, True, False)])
def test_alias_requires_active_staff_and_change_permission(source, django_user_model, active, staff, permission):
    plan, _ = source
    actor = django_user_model.objects.create_user(username="limited-alias", is_active=active, is_staff=staff)
    if permission:
        actor.user_permissions.add(Permission.objects.get(content_type__app_label="platform_api", codename="change_floorplan"))
    with pytest.raises(PermissionDenied):
        save_layout_name(plan.project, plan.pk, "Forbidden", actor)
    plan.refresh_from_db()
    assert plan.display_name == "" and not AuditEvent.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("actor", [None, AnonymousUser()])
def test_anonymous_alias_change_is_rejected(source, actor):
    plan, _ = source
    with pytest.raises(PermissionDenied):
        save_layout_name(plan.project, plan.pk, "Forbidden", actor)


@pytest.mark.django_db
def test_non_superuser_staff_with_change_permission_can_save(source, django_user_model):
    plan, _ = source
    actor = django_user_model.objects.create_user(username="alias-editor", is_staff=True)
    actor.user_permissions.add(Permission.objects.get(content_type__app_label="platform_api", codename="change_floorplan"))
    assert save_layout_name(plan.project, plan.pk, "Staff choice", actor).display_name == "Staff choice"


@pytest.mark.django_db
def test_lookup_is_scoped_to_applied_project_family(source):
    plan, actor = source
    dashboard = Project.objects.create(organization=plan.project.organization, submitted_name="Canonical")
    with pytest.raises(Http404):
        save_layout_name(dashboard, plan.pk, "Outside family", actor)
    merge = ProjectMerge.objects.create(source_project=plan.project, target_project=dashboard,
                                        state="proposed", reason_code="TEST")
    with pytest.raises(Http404):
        save_layout_name(dashboard, plan.pk, "Not yet merged", actor)
    merge.state = "applied"
    merge.save(update_fields=["state"])
    assert save_layout_name(dashboard, plan.pk, "Merged home", actor).display_name == "Merged home"
    with pytest.raises(Http404):
        save_layout_name(dashboard, uuid4(), "Missing", actor)


@pytest.mark.django_db
def test_invalid_input_and_audit_failure_do_not_change_alias(source, monkeypatch):
    plan, actor = source
    for plan_id, alias in [("not-an-id", "Name"), (plan.pk, "a" * 161), (plan.pk, "Bad\nname")]:
        with pytest.raises(ValidationError):
            save_layout_name(plan.project, plan_id, alias, actor)
    monkeypatch.setattr(AuditEvent.objects, "create", Mock(side_effect=RuntimeError("Audit unavailable")))
    with pytest.raises(RuntimeError, match="Audit unavailable"):
        save_layout_name(plan.project, plan.pk, "Must roll back", actor)
    plan.refresh_from_db()
    assert plan.display_name == "" and not AuditEvent.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["ai", "script"])
def test_agent_upsert_preserves_saved_alias(source, monkeypatch, mode):
    plan, actor = source
    name = "Known layout"
    plan.plan_name = f"{name} [AI {plan.workflow_run_id}]" if mode == "ai" else name
    plan.save(update_fields=["plan_name"])
    save_layout_name(plan.project, plan.pk, "Garden home", actor)
    agent = FloorPlanAgent(Mock())
    monkeypatch.setattr(agent, "_provider_metadata", lambda: {"extractionMode": mode})
    candidate = FloorPlanCandidate(page_number=1, plan_name=name, configuration_type="2BHK",
        carpet_area_sqft=None, dimensions=[], confidence=.9, evidence_text="Refreshed", asset=plan.asset, plan_scope="flat")
    persisted = agent._persist(plan.workflow_run, [candidate])
    assert len(persisted) == 1 and persisted[0].pk == plan.pk
    plan.refresh_from_db()
    assert plan.display_title == "Garden home" and plan.configuration_type == "2BHK"
    assert plan.raw_extraction["sourcePlanName"] == name


@pytest.mark.django_db
def test_placeholder_upsert_preserves_saved_alias(source, monkeypatch):
    plan, actor = source
    plan.plan_name = "Floor plan page 1"
    plan.save(update_fields=["plan_name"])
    save_layout_name(plan.project, plan.pk, "Reviewer title", actor)
    agent = FloorPlanAgent(Mock())
    monkeypatch.setattr(agent, "_provider_metadata", lambda: {"extractionMode": "ai"})
    plans = agent._persist_vision_placeholders(plan.workflow_run)
    assert len(plans) == 1 and plans[0].pk == plan.pk
    assert plans[0].display_name == "Reviewer title"


@pytest.mark.django_db
@pytest.mark.parametrize("rename_during_extraction", [False, True])
def test_crop_evaluation_keeps_alias_even_when_saved_during_model_call(source, monkeypatch, rename_during_extraction):
    from platform_api import layout_reanalysis

    plan, actor = source
    save_layout_name(plan.project, plan.pk, "Before extraction", actor)
    operation = Operation.objects.create(owner=actor, kind="floor_plan_crop_evaluation", state="pending")
    candidate = FloorPlanCandidate(page_number=1, plan_name="New extracted title", configuration_type="2BHK",
        carpet_area_sqft=None, dimensions=[], confidence=.9, evidence_text="Refreshed", asset=plan.asset, plan_scope="flat")
    provider = Mock()

    def analyze(*_args):
        if rename_during_extraction:
            save_layout_name(plan.project, plan.pk, "During extraction", actor)
        return {"pageType": "unit_layout", "visualEvidence": "One dwelling"}

    provider.analyze_classified_plan.side_effect = analyze
    monkeypatch.setattr(layout_reanalysis, "get_floor_plan_vision_provider", lambda _: provider)
    monkeypatch.setattr(FloorPlanAgent, "_vision_candidates", lambda *_args: [candidate])
    monkeypatch.setattr(FloorPlanAgent, "_provider_metadata", lambda _self: {"extractionMode": "ai"})
    evaluated = layout_reanalysis.evaluate_layout_crop({
        "floorPlanId": str(plan.pk), "assetId": str(plan.asset_id), "assetSha256": plan.asset.sha256,
        "operationId": str(operation.pk),
    })
    expected = "During extraction" if rename_during_extraction else "Before extraction"
    assert evaluated.pk == plan.pk and evaluated.display_name == expected
    assert evaluated.plan_name == "New extracted title" and evaluated.display_title == expected
    plan.refresh_from_db()
    assert plan.display_name == expected


@pytest.mark.django_db
@pytest.mark.parametrize("alias,expected", [("", "Full floor"), ("Garden home", "Garden home")])
def test_public_serializer_and_metrics_expose_display_title_not_internal_key(source, alias, expected):
    plan, actor = source
    plan.plan_name = f"Full floor [AI {GENERATED_ID}]"
    plan.save(update_fields=["plan_name"])
    plan = save_layout_name(plan.project, plan.pk, alias, actor)
    serializer = FloorPlanSerializer(plan)
    rendered = json.loads(CamelCaseJSONRenderer().render(serializer.data))
    assert rendered["planName"] == rendered["displayTitle"] == expected
    assert rendered["displayName"] == alias
    assert serializer.fields["displayName"].read_only
    assert serializer.fields["plan_name"].read_only
    assert "rawExtraction" not in rendered and plan.plan_name not in rendered.values()
    metrics = layout_metrics(plan, {"effectiveParcel": {"value": None}})
    assert metrics["planName"] == metrics["displayTitle"] == expected
    assert metrics["displayName"] == alias


@pytest.mark.django_db
def test_generated_json_and_csv_use_alias_and_escape_spreadsheet_formulas(source):
    plan, actor = source
    alias = '=HYPERLINK("https://example.invalid")'
    plan = save_layout_name(plan.project, plan.pk, alias, actor)
    run = plan.workflow_run.ingestion_run
    output = {"parcelDetails": {"effectiveParcel": {"value": None}, "towerUnitsAndLifts": []},
              "nearestMetro": {"walkingMinutes": None}, "nearestLocalStation": {"walkingMinutes": None}}
    outputs = store_outputs(run, output, [plan])
    with storage.open(outputs["layout-metrics"]["objectKey"], "rb") as stream:
        metrics = json.load(stream)
    assert metrics["layouts"][0]["displayTitle"] == alias
    with storage.open(outputs["layout-metrics-csv"]["objectKey"], "rb") as stream:
        rows = list(csv.DictReader(io.StringIO(stream.read().decode("utf-8-sig"))))
    assert rows[0]["planName"] == rows[0]["displayName"] == rows[0]["displayTitle"] == "'" + alias


@pytest.mark.django_db
def test_confirmation_template_escapes_alias(source):
    plan, actor = source
    plan = save_layout_name(plan.project, plan.pk, "<script>alert(1)</script>", actor)
    rendered = render_to_string("admin/platform_api/floor_plan_removal_confirmation.html", {
        "original": plan, "dashboard_project": plan.project,
    })
    assert "<script>alert(1)</script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered