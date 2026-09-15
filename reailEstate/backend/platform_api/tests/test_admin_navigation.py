from datetime import timedelta
from urllib.parse import urlencode, urlsplit, parse_qs
from uuid import uuid4

import pytest
from django.utils import timezone
from django.urls import reverse

from platform_api.models import (
    Organization, Project, EvidenceDocument, IngestionRun, AgentWorkflowRun, FloorPlan, FloorPlanAsset,
)


pytestmark = pytest.mark.django_db


@pytest.fixture
def records(django_user_model, settings):
    settings.SECURE_SSL_REDIRECT = False
    settings.ALLOWED_HOSTS = ["testserver"]
    owner = django_user_model.objects.create_superuser(username="navigation-admin", password="test-only-password")
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Navigation"), submitted_name="Navigation")
    doc = EvidenceDocument.objects.create(project=project, uploaded_by=owner, original_name="plan.pdf",
        object_key=f"quarantine/{uuid4()}", declared_media_type="application/pdf", byte_length=1,
        sha256="0" * 64, retention_until=timezone.now() + timedelta(days=7))
    run = IngestionRun.objects.create(project=project, document=doc)
    workflow = AgentWorkflowRun.objects.create(project=project, document=doc, ingestion_run=run)
    plans, assets = [], []
    for page in (1, 2, 3):
        asset = FloorPlanAsset.objects.create(project=project, document=doc, workflow_run=workflow,
            page_number=page, object_key=f"derived/{uuid4()}.png", byte_length=1, sha256=str(page) * 64,
            pixel_width=100, pixel_height=100, detector_version="test", detection_confidence=0.5,
            source_bounds={"category": "amenities" if page == 3 else "floor-plans"})
        assets.append(asset)
        plans.append(FloorPlan.objects.create(project=project, document=doc, workflow_run=workflow,
            asset=asset, page_number=page, plan_name=f"Plan {page}", extractor_version="test",
            plan_scope="flat" if page == 3 else "floor"))
    return owner, project, plans, assets


def detail(client, model, item, filters=""):
    url = reverse(f"admin:platform_api_{model}_change", args=[item.pk])
    return client.get(url, {"_changelist_filters": filters} if filters else {})


def test_navigation_first_middle_last(client, records):
    owner, project, plans, _ = records
    client.force_login(owner)
    for index, plan in enumerate(plans):
        response = detail(client, "floorplan", plan)
        assert response.status_code == 200
        nav = response.context["image_navigation"]
        assert nav["position"] == index + 1
        assert nav["total"] == 3
        assert (nav["previous"] is None) == (index == 0)
        assert (nav["next"] is None) == (index == 2)
        assert b"Save any edits before navigating" in response.content
    nav = detail(client, "floorplan", plans[1]).context["image_navigation"]
    assert str(plans[0].id) in nav["previous"]
    assert str(plans[2].id) in nav["next"]


def test_navigation_preserves_project_scope_search_and_order(client, records):
    owner, project, plans, _ = records
    client.force_login(owner)
    filters = urlencode({"project__id__exact": project.id, "plan_scope__exact": "floor", "q": "Plan", "o": "-3"})
    response = detail(client, "floorplan", plans[1], filters)
    assert response.status_code == 200
    nav = response.context["image_navigation"]
    assert nav["total"] == 2
    assert nav["previous"] is None
    assert str(plans[0].pk) in nav["next"]
    assert parse_qs(urlsplit(nav["next"]).query)["_changelist_filters"] == [filters]
    assert parse_qs(urlsplit(nav["list"]).query)["q"] == ["Plan"]


def test_asset_category_filter_is_preserved(client, records):
    owner, _, _, assets = records
    client.force_login(owner)
    response = detail(client, "floorplanasset", assets[1], "category=floor-plans")
    nav = response.context["image_navigation"]
    assert nav["total"] == 2
    assert nav["previous"] is None
    assert str(assets[0].pk) in nav["next"]
    assert str(assets[2].pk) not in str(nav)


def test_nonmatching_scope_does_not_broaden_navigation(client, records):
    owner, _, plans, _ = records
    client.force_login(owner)
    assert detail(client, "floorplan", plans[0], "q=nonexistent").context["image_navigation"] is None


def test_staff_without_model_permission_cannot_navigate(client, records, django_user_model):
    _, _, plans, _ = records
    staff = django_user_model.objects.create_user(username="no-plan-access", is_staff=True)
    client.force_login(staff)
    assert detail(client, "floorplan", plans[0]).status_code == 403