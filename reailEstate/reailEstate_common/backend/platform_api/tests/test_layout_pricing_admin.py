from .workspace_helpers import workspace_request, media_action
from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from platform_api.models import AuditEvent, FloorPlan

from .test_floor_plan_layout_jobs import source


pytestmark = pytest.mark.django_db


@pytest.fixture
def priced_layout(source):
    plan, actor = source
    plan.plan_scope = "flat"
    plan.price = Decimal("15000000.00")
    plan.save()
    return plan, actor


def price_data(plan, price):
    return {
        "expectedUpdatedAt": plan.updated_at.isoformat(), "price": price if price != "" else None,
    }


def dashboard_url(plan):
    return reverse("admin:platform_api_project_media_dashboard", args=[plan.project_id])


def test_dashboard_saves_clears_and_displays_layout_price(priced_layout, client):
    plan, actor = priced_layout
    client.force_login(actor)
    assert workspace_request(client, plan.project, '/media').json()['projectPrice'] == 'INR 15,000,000.00'
    for price, expected in [('17500000.50', Decimal('17500000.50')), ('', None)]:
        response = workspace_request(client, plan.project, f'/layouts/{plan.pk}', price_data(plan, price), method='patch')
        assert response.status_code == 200, response.content
        plan.refresh_from_db()
        assert plan.price == expected
    assert workspace_request(client, plan.project, '/media').json()['projectPrice'] == 'contact to know'
    assert AuditEvent.objects.filter(entity_id=plan.pk, action='layout.price_changed').count() == 2


@pytest.mark.parametrize("price", ["0", "-1", "NaN", "Infinity", "123.456", "100000000000000"])
def test_dashboard_rejects_invalid_prices(priced_layout, client, price):
    plan, actor = priced_layout
    client.force_login(actor)
    response = workspace_request(client, plan.project, f'/layouts/{plan.pk}', price_data(plan, price), method='patch')
    assert response.status_code == 400
    plan.refresh_from_db()
    assert plan.price == Decimal('15000000.00')
    assert not AuditEvent.objects.filter(entity_id=plan.pk, action='layout.price_changed').exists()


def test_dashboard_rejects_stale_price_edits(priced_layout, client):
    plan, actor = priced_layout
    client.force_login(actor)
    stale = price_data(plan, '19000000')
    assert workspace_request(client, plan.project, f'/layouts/{plan.pk}', price_data(plan, '18000000'), method='patch').status_code == 200
    response = workspace_request(client, plan.project, f'/layouts/{plan.pk}', stale, method='patch')
    assert response.status_code == 409
    plan.refresh_from_db()
    assert plan.price == Decimal('18000000.00')


def test_price_edit_requires_floor_plan_permission(priced_layout, client):
    plan, actor = priced_layout
    actor.is_superuser = False
    actor.save()
    actor.user_permissions.set(Permission.objects.filter(content_type__app_label='platform_api', codename__in=['view_project','change_project']))
    client.force_login(actor)
    response = workspace_request(client, plan.project, f'/layouts/{plan.pk}', price_data(plan, '18000000'), method='patch')
    assert response.status_code == 403
    plan.refresh_from_db()
    assert plan.price == Decimal('15000000.00')


def test_price_edit_requires_csrf(priced_layout):
    plan, actor = priced_layout
    client = Client(enforce_csrf_checks=True)
    client.force_login(actor)
    assert workspace_request(client, plan.project, f'/layouts/{plan.pk}', price_data(plan, '18000000'), method='patch').status_code == 403


def test_project_admin_and_dashboard_show_derived_range(priced_layout, client):
    plan, actor = priced_layout
    FloorPlan.objects.create(project=plan.project, document=plan.document, workflow_run=plan.workflow_run, asset=plan.asset,
        page_number=1, plan_scope='flat', plan_name='Second layout', price=Decimal('20000000'), extractor_version='test')
    client.force_login(actor)
    assert workspace_request(client, plan.project, '/media').json()['projectPrice'] == 'INR 15,000,000.00 - INR 20,000,000.00'
    response = client.get(reverse('admin:platform_api_project_change', args=[plan.project_id]))
    assert response.status_code == 200
    assert b'INR 15,000,000.00 - INR 20,000,000.00' in response.content


def test_project_layout_summary_combines_previews_prices_and_carpet_areas(priced_layout, client):
    plan, actor = priced_layout
    plan.display_name = "<East residence>"
    plan.configuration_type = "2BHK"
    plan.carpet_area_sqft = Decimal("745.50")
    plan.save()
    unknown = FloorPlan.objects.create(
        project=plan.project, document=plan.document, workflow_run=plan.workflow_run,
        page_number=2, plan_scope="flat", plan_name="Area and price pending", extractor_version="test",
    )
    for name, fields in [
        ("Inactive", {"active": False}),
        ("Rejected", {"review_state": "rejected"}),
        ("Floor source", {"plan_scope": "floor"}),
    ]:
        FloorPlan.objects.create(
            project=plan.project, document=plan.document, workflow_run=plan.workflow_run,
            page_number=3, plan_name=name, extractor_version="test",
            **({"plan_scope": "flat"} | fields),
        )
    before = list(FloorPlan.objects.order_by("pk").values())
    client.force_login(actor)
    response = client.get(reverse("admin:platform_api_project_change", args=[plan.project_id]))
    assert response.status_code == 200
    assert {item.pk for item in response.context["project_layouts"]} == {plan.pk, unknown.pk}
    html = response.content.decode()
    assert "Layouts, prices and sizes" in html
    assert "Carpet area (sq ft)" in html and "Total price (INR)" in html
    assert "745.5" in html and "15,000,000.00" in html
    assert "Unknown" in html and "contact to know" in html
    assert "&lt;East residence&gt;" in html and "<East residence>" not in html
    assert reverse("floor-plan-asset-media", args=[plan.asset_id]) in html
    assert reverse("admin:platform_api_floorplan_change", args=[plan.pk]) in html
    assert 'class="project-address-disclosure">' in html
    assert "field-layout_price_range" not in html
    assert html.index('id="project-layouts-heading"') < html.index('class="project-address-disclosure"')
    assert list(FloorPlan.objects.order_by("pk").values()) == before


def test_project_layout_summary_respects_record_permissions(priced_layout, client):
    plan, actor = priced_layout
    actor.is_superuser = False
    actor.save()
    actor.user_permissions.set(Permission.objects.filter(
        content_type__app_label="platform_api", codename="view_project",
    ))
    client.force_login(actor)
    response = client.get(reverse("admin:platform_api_project_change", args=[plan.project_id]))
    assert response.status_code == 200
    assert b"15,000,000.00" in response.content
    assert reverse("admin:platform_api_floorplan_change", args=[plan.pk]).encode() not in response.content
    assert b'class="project-layout-preview"' not in response.content


def test_active_toggle_updates_project_price(priced_layout, client):
    plan, actor = priced_layout
    client.force_login(actor)
    response = media_action(client, plan, 'set-active', active=False)
    assert response.status_code == 202, response.content
    assert workspace_request(client, plan.project, '/media').json()['projectPrice'] == 'contact to know'
    plan.refresh_from_db()
    assert plan.price == Decimal('15000000.00')


def test_floor_plan_source_has_no_price_editor(source, client):
    plan, actor = source
    client.force_login(actor)
    assert workspace_request(client, plan.project, '/layouts').json() == []
    assert workspace_request(client, plan.project, '/media').json()['projectPrice'] == 'contact to know'
    response = workspace_request(client, plan.project, f'/layouts/{plan.pk}', {'price':'100', 'expectedUpdatedAt':plan.updated_at.isoformat()}, method='patch')
    assert response.status_code == 404
