from decimal import Decimal
from unittest.mock import Mock

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404

from platform_api.floor_plan_agent import FloorPlanAgent, FloorPlanCandidate
from platform_api.layout_configurations import project_layout_configurations
from platform_api.layout_pricing import annotate_project_prices, project_price_range, save_layout_price
from platform_api.models import AuditEvent, Building, FloorPlan, HomeConfiguration, Project, ProjectMerge, ReviewTask
from platform_api.serializers import ProjectListSerializer
from platform_api.layout_matching import score_layouts

from .test_floor_plan_layout_jobs import source


pytestmark = pytest.mark.django_db


@pytest.fixture
def layout(source):
    plan, actor = source
    plan.project.status = "published"
    plan.project.save()
    plan.plan_scope = "flat"
    plan.price = Decimal("15000000.50")
    plan.review_state = "approved"
    plan.save()
    return plan, actor


def another(plan, **values):
    return FloorPlan.objects.create(
        project=plan.project, document=plan.document, workflow_run=plan.workflow_run,
        asset=plan.asset, page_number=values.pop("page_number", plan.page_number),
        plan_scope=values.pop("plan_scope", "flat"), plan_name=f"Layout {FloorPlan.objects.count()}",
        review_state=values.pop("review_state", "approved"), extractor_version="test", **values,
    )


def test_range_uses_only_layout_prices_and_handles_missing(layout):
    plan, _ = layout
    another(plan, price="22000000.00")
    another(plan)
    assert project_price_range(plan.project) == (Decimal("15000000.50"), Decimal("22000000.00"))
    annotated = annotate_project_prices(Project.objects.all()).get(pk=plan.project_id)
    assert (annotated.layout_price_min, annotated.layout_price_max) == project_price_range(plan.project)
    FloorPlan.objects.filter(project=plan.project).update(price=None)
    HomeConfiguration.objects.create(
        building=Building.objects.create(project=plan.project, name="A"), configuration_type="2BHK",
        carpet_area_min=700, carpet_area_max=800, price_min=1000,
    )
    assert project_price_range(plan.project) == (None, None)
    assert ProjectListSerializer(plan.project).data["price_min"] is None


@pytest.mark.parametrize("changes", [
    {"active": False}, {"review_state": "removed_by_user"}, {"review_state": "replaced_by_user"},
    {"review_state": "superseded_page_candidate"}, {"review_state": "rejected"},
    {"plan_scope": "floor"}, {"plan_scope": "unknown"},
])
def test_ineligible_layouts_never_set_range(layout, changes):
    plan, _ = layout
    another(plan, price="1.00", **changes)
    assert project_price_range(plan.project, can_review=True) == (plan.price, plan.price)


@pytest.mark.parametrize("page_number", [1, None])
def test_inactive_source_excludes_child_price(layout, page_number):
    plan, _ = layout
    plan.page_number = page_number
    plan.save()
    another(plan, plan_scope="floor", active=False)
    assert project_price_range(plan.project, can_review=True) == (None, None)


def test_quarantined_document_and_unready_asset_do_not_set_price(layout):
    plan, _ = layout
    plan.document.scan_status = "pending"
    plan.document.save()
    assert project_price_range(plan.project, can_review=True) == (None, None)
    plan.document.scan_status = "clean"
    plan.document.save()
    plan.asset.status = "failed"
    plan.asset.save()
    assert project_price_range(plan.project, can_review=True) == (None, None)


def test_applied_merge_contributes_to_canonical_project_only(layout):
    plan, actor = layout
    target = Project.objects.create(
        organization=plan.project.organization, submitted_name="Canonical", status="published",
    )
    with pytest.raises(Http404):
        save_layout_price(target, plan.pk, Decimal("20000000"), actor, expected_updated_at=plan.updated_at)
    merge = ProjectMerge.objects.create(source_project=plan.project, target_project=target,
                                        state="proposed", reason_code="TEST")
    assert project_price_range(target) == (None, None)
    merge.state = "applied"
    merge.save()
    plan.project.status = "merged"
    plan.project.save()
    assert project_price_range(target) == (plan.price, plan.price)
    assert project_price_range(plan.project, can_review=True) == (None, None)
    annotated = annotate_project_prices(Project.objects.all()).get(pk=target.pk)
    assert annotated.layout_price_min == plan.price


def test_editing_price_requires_fresh_review_and_audit(layout):
    plan, actor = layout
    updated = save_layout_price(plan.project, plan.pk, Decimal("20000000"), actor,
                                expected_updated_at=plan.updated_at)
    assert updated.review_state == "needs_review"
    assert project_price_range(plan.project) == (None, None)
    assert project_price_range(plan.project, can_review=True) == (Decimal("20000000"), Decimal("20000000"))
    assert ReviewTask.objects.filter(resolution__source="manual_pricing",
                                     resolution__floorPlanId=str(plan.pk)).exists()
    assert AuditEvent.objects.filter(action="layout.price_changed", entity_id=plan.pk).exists()


@pytest.mark.parametrize("price", [Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity"),
                                  Decimal("12.345"), Decimal("100000000000000")])
def test_service_rejects_invalid_prices(layout, price):
    plan, actor = layout
    with pytest.raises(ValidationError):
        save_layout_price(plan.project, plan.pk, price, actor, expected_updated_at=plan.updated_at)
    plan.refresh_from_db()
    assert plan.price == Decimal("15000000.50")


def test_database_rejects_nonpositive_prices(layout):
    plan, _ = layout
    with pytest.raises(IntegrityError), transaction.atomic():
        FloorPlan.objects.filter(pk=plan.pk).update(price=0)


def test_audit_failure_rolls_back_price(layout, monkeypatch):
    plan, actor = layout
    monkeypatch.setattr(AuditEvent.objects, "create", Mock(side_effect=RuntimeError("Audit unavailable")))
    with pytest.raises(RuntimeError, match="Audit unavailable"):
        save_layout_price(plan.project, plan.pk, Decimal("20000000"), actor,
                          expected_updated_at=plan.updated_at)
    plan.refresh_from_db()
    assert plan.price == Decimal("15000000.50") and plan.review_state == "approved"
    assert not ReviewTask.objects.filter(resolution__source="manual_pricing").exists()


def test_catalogue_detail_gallery_and_budget_filter_use_layout_price(layout, client):
    plan, _ = layout
    another(plan, price="20000000.00")
    response = client.get("/api/v1/projects/")
    assert response.status_code == 200
    entry = next(item for item in response.json()["results"] if item["id"] == str(plan.project_id))
    assert (entry["priceMin"], entry["priceMax"]) == ("15000000.50", "20000000.00")
    detail = client.get(f"/api/v1/projects/{plan.project_id}/").json()
    assert (detail["priceMin"], detail["priceMax"]) == ("15000000.50", "20000000.00")
    gallery = client.get(f"/api/v1/projects/{plan.project_id}/gallery").json()
    prices = {item["id"]: item["price"] for image in gallery["results"] for item in image["layouts"]}
    assert prices[str(plan.pk)] == "15000000.50"
    assert client.get("/api/v1/projects/", {"allInPriceMaximum": "15000000"}).json()["results"] == []
    assert len(client.get("/api/v1/projects/", {"allInPriceMaximum": "15000001"}).json()["results"]) == 1
    assert client.get("/api/v1/projects/", {"allInPriceMaximum": "NaN"}).status_code == 400


def test_configurations_are_derived_from_layout_area_and_price(layout, client):
    plan, _ = layout
    plan.configuration_type = "2 BHK"
    plan.carpet_area_sqft = Decimal("700.50")
    plan.bathroom_count = 2
    plan.display_name = "East residence"
    plan.dimensions = [{"room": "Living", "lengthFt": 12, "widthFt": 10}]
    plan.save()
    second = another(
        plan, configuration_type="2BHK", carpet_area_sqft="850.00",
        price="18000000.00", bathroom_count=3, display_name="West residence",
    )
    third = another(
        plan, configuration_type="", carpet_area_sqft=None,
        price=None, bathroom_count=None,
    )
    configurations = project_layout_configurations(plan.project)
    assert len(configurations) == 3
    by_id = {row["id"]: row for row in configurations}
    assert by_id[str(plan.pk)]["name"] == "East residence"
    assert by_id[str(plan.pk)]["carpet_area_min"] == by_id[str(plan.pk)]["carpet_area_max"] == Decimal("700.50")
    assert by_id[str(second.pk)]["carpet_area_min"] == Decimal("850.00")
    assert by_id[str(second.pk)]["configuration_type"] == "2BHK"
    assert by_id[str(third.pk)]["name"] == third.display_title
    assert by_id[str(third.pk)]["carpet_area_min"] is None
    assert by_id[str(third.pk)]["price_min"] is None
    detail = client.get(f"/api/v1/projects/{plan.project_id}/").json()
    assert detail["configurations"][0] == {
        "id": str(plan.pk), "name": "East residence", "configurationType": "2BHK",
        "carpetAreaMin": 700.5, "carpetAreaMax": 700.5, "roomRectangleSumSqFt": 120,
        "priceMin": 15000000.5, "priceMax": 15000000.5, "currency": "INR",
        "bathrooms": 2, "balconies": None, "parkingSpaces": None,
        "roomDimensions": {}, "factSetVersion": 0,
    }
    plan.refresh_from_db()
    assert "areaSqFt" not in plan.dimensions[0]


def test_configuration_rows_keep_four_layouts_with_two_bhk_types_and_duplicate_aliases(layout, client):
    plan, _ = layout
    plan.configuration_type = "1BHK"
    plan.display_name = "Unit A"
    plan.save()
    plans = [
        plan,
        another(plan, configuration_type="1 BHK", display_name="Unit B"),
        another(plan, configuration_type="2BHK", display_name="Unit C"),
        another(plan, configuration_type="2 BHK", display_name="Unit C"),
    ]
    detail = client.get(f"/api/v1/projects/{plan.project_id}/").json()
    rows = detail["configurations"]
    assert len(rows) == 4
    assert {row["id"] for row in rows} == {str(item.pk) for item in plans}
    assert [row["name"] for row in rows] == ["Unit A", "Unit B", "Unit C", "Unit C"]
    assert [row["configurationType"] for row in rows] == ["1BHK", "1BHK", "2BHK", "2BHK"]


def test_configuration_visibility_matches_layout_review_policy(layout, settings):
    plan, _ = layout
    plan.configuration_type = "1BHK"
    plan.carpet_area_sqft = Decimal("500")
    plan.review_state = "needs_review"
    plan.save()
    settings.PROJECT_PUBLIC_PREVIEW_ENABLED = False
    assert ProjectListSerializer(plan.project).data["configurations"] == []
    settings.PROJECT_PUBLIC_PREVIEW_ENABLED = True
    assert ProjectListSerializer(plan.project).data["configurations"][0]["carpet_area_min"] == Decimal("500")


@pytest.mark.parametrize("preview", [False, True])
def test_processing_prices_respect_public_preview(layout, client, settings, preview):
    plan, _ = layout
    settings.PROJECT_PUBLIC_PREVIEW_ENABLED = preview
    plan.project.status = "needs_review"
    plan.project.save()
    plan.review_state = "needs_review"
    plan.save()
    entry = client.get("/api/v1/projects/", {"includeProcessing": "true"}).json()["results"][0]
    assert entry["priceMin"] == ("15000000.50" if preview else None)
    assert entry["priceMax"] == entry["priceMin"]


def test_unreviewed_prices_are_not_public_when_preview_disabled(layout, client):
    plan, _ = layout
    plan.review_state = "needs_review"
    plan.save()
    entry = client.get("/api/v1/projects/").json()["results"][0]
    assert entry["priceMin"] is None and entry["priceMax"] is None


def test_budget_scoring_uses_layout_prices_without_legacy_fallback(layout):
    plan, _ = layout
    profile = {"budget": {"purchaseMaximum": 10000000, "isHardConstraint": True}}
    assert score_layouts(profile, [plan], strict=True) == ([], "OVER_HARD_BUDGET")
    candidates, failure = score_layouts(profile, [plan], strict=False)
    assert failure is None and candidates[0]["values"]["budget"] is not None
    plan.price = None
    plan.save()
    assert score_layouts(profile, [plan], strict=True) == ([], "LAYOUT_REQUIRED_DATA_UNAVAILABLE")
    candidates, failure = score_layouts(profile, [plan], strict=False)
    assert failure is None and candidates[0]["values"]["budget"] is None


@pytest.mark.parametrize("mode", ["ai", "script"])
def test_extraction_upsert_preserves_manual_price(layout, monkeypatch, mode):
    plan, _ = layout
    name = "Known layout"
    plan.plan_name = f"{name} [AI {plan.workflow_run_id}]" if mode == "ai" else name
    plan.save()
    agent = FloorPlanAgent(Mock())
    monkeypatch.setattr(agent, "_provider_metadata", lambda: {"extractionMode": mode})
    candidate = FloorPlanCandidate(
        page_number=1, plan_name=name, configuration_type="2BHK", carpet_area_sqft=None,
        dimensions=[], confidence=.9, evidence_text="Refreshed", asset=plan.asset, plan_scope="flat",
    )
    persisted = agent._persist(plan.workflow_run, [candidate])
    assert len(persisted) == 1 and persisted[0].pk == plan.pk
    plan.refresh_from_db()
    assert plan.price == Decimal("15000000.50")
