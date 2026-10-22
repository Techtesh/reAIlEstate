from uuid import uuid4

import pytest
from django.contrib import admin
from django.test import RequestFactory
from django.urls import reverse
from rest_framework.test import APIClient

from platform_api.admin import FloorPlanAdmin
from platform_api.models import AuditEvent, FloorPlan, FloorPlanAsset, OrganizationMembership
from .test_floor_plan_layout_jobs import source


pytestmark = pytest.mark.django_db


def layout(source_plan, **values):
    return FloorPlan.objects.create(
        project=values.pop("project", source_plan.project),
        document=values.pop("document", source_plan.document),
        workflow_run=source_plan.workflow_run,
        asset=values.pop("asset", source_plan.asset),
        page_number=values.pop("page_number", source_plan.page_number),
        plan_scope=values.pop("plan_scope", "flat"),
        plan_name=str(uuid4()), extractor_version="test", **values,
    )


def asset(source_plan, **values):
    return FloorPlanAsset.objects.create(
        project=values.pop("project", source_plan.project),
        document=values.pop("document", source_plan.document),
        workflow_run=source_plan.workflow_run,
        page_number=values.pop("page_number", source_plan.page_number),
        asset_type=values.pop("asset_type", "unit_crop"),
        object_key=str(uuid4()), sha256=uuid4().hex * 2, byte_length=1,
        pixel_width=100, pixel_height=100,
        detector_version="test", **values,
    )


@pytest.fixture(params=["admin", "builder"])
def toggle(source, request):
    plan, actor = source
    client = APIClient()
    if request.param == "builder":
        actor.is_staff = actor.is_superuser = False
        actor.save()
        OrganizationMembership.objects.create(user=actor, organization=plan.project.organization, role="owner")
    client.force_login(actor)

    def change(target, active, *, version=None, request_id=None):
        if request.param == "builder":
            return client.post(f"/api/v1/builder/projects/{plan.project_id}/media/actions", {
                "action": "set-active", "active": active, "confirm": True,
                "requestId": request_id or str(uuid4()),
                "targets": [{"planId": str(target.pk),
                             "expectedUpdatedAt": version or target.updated_at.isoformat()}],
            }, format="json")
        from .workspace_helpers import workspace_request
        return workspace_request(client,plan.project,'/media/actions',{
            'action':'set-active','active':active,'confirm':True,'requestId':request_id or str(uuid4()),
            'targets':[{'planId':str(target.pk),'expectedUpdatedAt':version or target.updated_at.isoformat()}]},method='post')


    return plan, actor, change, request.param


def test_deactivation_persists_children_and_reactivation_is_parent_only(toggle):
    plan, _, change, channel = toggle
    child = layout(plan, review_state="approved")
    child_version = child.updated_at
    response = change(plan, False)
    assert response.status_code in {200, 202}, response.content
    plan.refresh_from_db()
    child.refresh_from_db()
    assert not plan.active and not child.active
    assert child.updated_at > child_version
    assert child.review_state == "needs_review"
    audits = AuditEvent.objects.filter(action="floor_plan.active_changed")
    assert audits.count() == 2
    assert audits.get(entity_id=child.pk).metadata["sourceFloorPlanId"] == str(plan.pk)
    child_version = child.updated_at
    assert change(plan, True).status_code in {200, 202}
    plan.refresh_from_db()
    child.refresh_from_db()
    assert plan.active and not child.active and child.updated_at == child_version
    assert audits.count() == 3


def test_direct_floor_plan_admin_save_deactivates_generated_layouts(source):
    from .workspace_helpers import media_action
    from django.test import Client
    plan, actor = source
    child = layout(plan, review_state='approved')
    client = Client(); client.force_login(actor)
    response = media_action(client, plan, 'set-active', active=False)
    assert response.status_code == 202, response.content
    plan.refresh_from_db(); child.refresh_from_db()
    assert not plan.active and not child.active
    assert child.review_state == 'needs_review'
    assert AuditEvent.objects.filter(action='floor_plan.active_changed', entity_id__in=[plan.pk,child.pk]).count() == 2


def test_deactivation_rejects_stale_parent_without_changing_children(toggle):
    plan, _, change, _ = toggle
    child = layout(plan)
    response = change(plan, False, version="2000-01-01T00:00:00+00:00")
    assert response.status_code == 409
    child.refresh_from_db()
    assert child.active
    assert not AuditEvent.objects.filter(action="floor_plan.active_changed").exists()


def test_deactivation_follows_ancestry_but_not_sibling_floor_or_other_page(toggle):
    plan, _, change, _ = toggle
    sibling = layout(plan, plan_scope="floor", asset=asset(plan, asset_type="plan_crop"))
    own = layout(plan, asset=asset(plan, source_bounds={"sourcePlanId": str(plan.pk)}))
    sibling_child = layout(plan, asset=asset(plan, source_bounds={"sourcePlanId": str(sibling.pk)}))
    sibling_asset_child = layout(plan, asset=asset(plan, source_bounds={"sourceAssetId": str(sibling.asset_id)}))
    intermediary = asset(plan, source_bounds={"sourceAssetId": str(plan.asset_id)})
    descendant = layout(plan, asset=asset(plan, source_bounds={"sourceAssetId": str(intermediary.pk)}))
    other_page = layout(plan, page_number=2)
    orphan = layout(plan, asset=asset(plan, asset_type="page_render"))
    invalid_source = layout(plan, asset=asset(plan, source_bounds={"sourceAssetId": str(uuid4())}))
    response = change(plan, False)
    assert response.status_code in {200, 202}, response.content
    for child in (own, descendant):
        child.refresh_from_db()
        assert not child.active
    for child in (sibling, sibling_child, sibling_asset_child, other_page, orphan, invalid_source):
        child.refresh_from_db()
        assert child.active


def test_manual_page_crops_with_a_sole_floor_follow_deactivation(toggle):
    plan, _, change, _ = toggle
    page = asset(plan, asset_type="page_render")
    manual = layout(plan, asset=asset(plan, source_bounds={"sourceAssetId": str(page.pk)}))
    response = change(plan, False)
    assert response.status_code in {200, 202}, response.content
    manual.refresh_from_db()
    assert not manual.active


def test_deactivation_does_not_change_foreign_project_or_inactive_layouts(toggle):
    from platform_api.models import Organization, Project

    plan, _, change, _ = toggle
    foreign_project = Project.objects.create(
        organization=Organization.objects.create(legal_name="Another organization"), submitted_name="Foreign project",
    )
    foreign = layout(plan, project=foreign_project)
    inactive = layout(plan, active=False)
    inactive_version = inactive.updated_at
    response = change(plan, False)
    assert response.status_code in {200, 202}, response.content
    foreign.refresh_from_db()
    inactive.refresh_from_db()
    assert foreign.active
    assert not inactive.active and inactive.updated_at == inactive_version
    assert not AuditEvent.objects.filter(entity_id__in=[foreign.pk, inactive.pk]).exists()


def test_deactivation_prefers_explicit_plan_id_over_shared_asset(toggle):
    plan, _, change, _ = toggle
    sibling = layout(plan, plan_scope="floor")
    own = layout(plan, raw_extraction={"sourcePlanId": str(plan.pk)})
    sibling_child = layout(plan, raw_extraction={"sourcePlanId": str(sibling.pk)})
    ambiguous = layout(plan)
    response = change(plan, False)
    assert response.status_code in {200, 202}, response.content
    own.refresh_from_db()
    sibling_child.refresh_from_db()
    ambiguous.refresh_from_db()
    assert not own.active
    assert sibling_child.active and ambiguous.active
