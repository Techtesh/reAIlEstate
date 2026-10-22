"""Integration contracts for media controls; no live models or destructive actions."""

from unittest.mock import Mock

import pytest
from django.contrib import admin
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from django.urls import reverse

from platform_api.admin import FloorPlanAdmin, ProjectAdmin
from platform_api.models import Project, LayoutCropDraft, OutboxMessage
from .test_crop_cart import source, REGION

pytestmark = pytest.mark.django_db


def request(actor, method="get", data=None):
    factory = RequestFactory()
    req = factory.post("/?section=layouts", data or {}) if method == "post" else factory.get("/?section=layouts")
    req.user = actor
    req.session = {}
    req._messages = FallbackStorage(req)
    return req


def test_dashboard_shows_crop_previews_not_full_floor_sources(source):
    from platform_api.crop_cart import stage_crops, attach_drafts
    project, plan, actor = source
    plan.plan_scope = "floor"
    plan.save()
    ids = stage_crops(plan, [REGION], actor)["draftIds"]
    attached = attach_drafts(project, actor, ids)
    response = ProjectAdmin(Project, admin.site).media_dashboard_view(request(actor), str(project.pk))
    response.render()
    context = response.context_data
    assert len(context["carousel"]) == 1
    assert str(plan.asset_id) not in context["carousel"][0]["imageUrl"]
    card = context["layouts"][0]
    assert str(card["plan"].pk) == attached[0]
    assert card["isCroppedUnit"]
    assert card["editCropUrl"] == reverse("admin:platform_api_floorplan_crop_update", args=[attached[0]])
    html = response.content.decode()
    assert 'form="layout-bulk-removal"' in html and "draft-cart-icon" in html
    assert "Location:" in html and "Queue extraction" in html


def test_dashboard_extract_action_only_queues_and_deduplicates(source, monkeypatch):
    project, plan, actor = source
    worker = Mock(side_effect=AssertionError("Click must not execute models or drain queue"))
    monkeypatch.setattr("platform_api.workers.process_pending_outbox", worker)
    monkeypatch.setattr("platform_api.admin.process_pending_outbox", worker)
    from platform_api import layout_reanalysis
    monkeypatch.setattr(layout_reanalysis, "resolve_vision_selection", lambda _: {"profileId": "test"})
    view = ProjectAdmin(Project, admin.site)
    for _ in range(3):
        response = view.media_dashboard_view(request(actor, "post", {
            "action": "run_layout_evaluation", "floor_plan_id": str(plan.pk),
        }), str(project.pk))
        assert response.status_code == 302
    assert OutboxMessage.objects.filter(payload__floorPlanId=str(plan.pk)).count() == 1
    worker.assert_not_called()


def test_global_cart_only_groups_current_owner_drafts(source):
    from platform_api.crop_cart import stage_crops
    project, plan, actor = source
    stage_crops(plan, [REGION], actor)
    response = ProjectAdmin(Project, admin.site).layout_cart_view(request(actor))
    response.render()
    assert response.context_data["cart_count"] == 1
    assert response.context_data["cart_projects"][0]["name"] == project.submitted_name
    assert "#draft-crop-cart" in response.context_data["cart_projects"][0]["url"]
    assert LayoutCropDraft.objects.count() == 1


def test_dashboard_cart_adds_selected_draft_through_admin_route(source, client):
    from platform_api.crop_cart import stage_crops

    project, plan, actor = source
    draft_id = stage_crops(plan, [REGION], actor)["draftIds"][0]
    client.force_login(actor)

    response = client.post(
        reverse("admin:platform_api_project_crop_cart", args=[project.pk]),
        {"draft_action": "add", "draft_ids": [draft_id]},
        follow=True,
    )

    assert response.status_code == 200
    assert not LayoutCropDraft.objects.filter(pk=draft_id).exists()
    assert b"Added 1 selected layout" in response.content


def test_dashboard_cart_explains_unselected_and_inapplicable_actions(source, client):
    from platform_api.crop_cart import stage_crops

    project, plan, actor = source
    draft_id = stage_crops(plan, [REGION], actor)["draftIds"][0]
    client.force_login(actor)
    url = reverse("admin:platform_api_project_crop_cart", args=[project.pk])

    response = client.post(url, {"draft_action": "add"}, follow=True)

    assert response.status_code == 200
    assert LayoutCropDraft.objects.filter(pk=draft_id).exists()
    assert b"Select at least one draft crop." in response.content
    assert b"Commit saved pages/updates (none available)" in response.content
    assert b"data-draft-cart-feedback" in response.content
    assert b'tabindex="-1" hidden' in response.content


def test_full_project_health_annotation_can_be_fetched(source):
    project, plan, actor = source
    plan.review_state = "needs_boundary_review"
    plan.save()
    # Fetch whole model, not only counts: catches Coalesce FK/UUID output-field bugs.
    obj = ProjectAdmin(Project, admin.site).get_object(request(actor), str(project.pk))
    assert obj.boundary_review_count == 1
    assert obj.layout_health_project_id == project.pk