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
    listing = client.get(reverse("admin:platform_api_floorplan_changelist"))
    page_column = listing.context["cl"].list_display.index("page_number")
    filters = urlencode({"project__id__exact": project.id, "plan_scope__exact": "floor", "q": "Plan", "o": f"-{page_column}"})
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


@pytest.mark.parametrize("organization_status", ["inactive", "suspended"])
@pytest.mark.parametrize("section", ["layouts", "floor-plans", "amenities", "project-images"])
def test_media_project_navigation_matches_list_and_skips_hidden_organizations(client, records, organization_status, section):
    owner, current, _, _ = records
    current.submitted_name = "Adani The Views"
    current.save(update_fields=["submitted_name"])
    previous = Project.objects.create(organization=current.organization, submitted_name="Ace Parkside")
    hidden = Project.objects.create(
        organization=Organization.objects.create(legal_name="DemoPins", status=organization_status),
        submitted_name="ADIJON",
    )
    following = Project.objects.create(organization=current.organization, submitted_name="Adityaraj Avenue")
    Project.objects.create(organization=current.organization, submitted_name="Abandoned merged source",
                           address={"mergedIntoProjectId": str(current.pk)})
    client.force_login(owner)
    listing = client.get(reverse("admin:platform_api_project_changelist"))
    listed_ids = list(listing.context["cl"].queryset.values_list("pk", flat=True))
    assert listed_ids == [previous.pk, current.pk, following.pk]
    url = reverse("admin:platform_api_project_media_dashboard", args=[current.pk])
    response = client.get(url, {"section": section})
    assert response.status_code == 200
    navigation = response.context["project_navigation"]
    assert navigation["position"] == listed_ids.index(current.pk) + 1 == 2
    assert navigation["total"] == listing.context["cl"].result_count == 3
    assert str(hidden.pk) not in str(navigation)
    assert str(previous.pk) in navigation["previous"]
    assert str(following.pk) in navigation["next"]
    assert parse_qs(urlsplit(navigation["next"]).query)["section"] == [section]
    next_response = client.get(navigation["next"])
    assert next_response.status_code == 200
    assert next_response.context["original"].pk == following.pk
    assert next_response.context["project_navigation"]["position"] == 3
    assert next_response.context["project_navigation"]["next"] is None
    previous_response = client.get(navigation["previous"])
    assert previous_response.status_code == 200
    assert previous_response.context["project_navigation"]["previous"] is None
    assert client.get(reverse("admin:platform_api_project_media_dashboard", args=[hidden.pk])).status_code == 404


def test_media_project_navigation_preserves_project_search_filter_order_and_entry_links(client, records):
    from django.contrib import admin
    owner, current, _, _ = records
    current.submitted_name = "Adani The Views"
    current.locality = "Ghatkopar"
    current.save(update_fields=["submitted_name", "locality"])
    for name in ["Ace Parkside", "Adityaraj Avenue"]:
        Project.objects.create(organization=current.organization, submitted_name=name, locality="Ghatkopar")
    Project.objects.create(organization=current.organization, submitted_name="Adani Elsewhere", locality="Vikhroli")
    client.force_login(owner)
    list_url = reverse("admin:platform_api_project_changelist")
    listing = client.get(list_url)
    column = listing.context["cl"].list_display.index("submitted_name")
    filters = urlencode({"q": "A", "locality__exact": "Ghatkopar", "o": f"-{column}", "page_size": "10"})
    listing = client.get(list_url + "?" + filters)
    assert listing.status_code == 200
    listed_ids = list(listing.context["cl"].queryset.values_list("pk", flat=True))
    assert len(listed_ids) == 3
    url = reverse("admin:platform_api_project_media_dashboard", args=[current.pk])
    response = client.get(url, {"section": "layouts", "_changelist_filters": filters})
    navigation = response.context["project_navigation"]
    assert navigation["position"] == listed_ids.index(current.pk) + 1
    assert navigation["total"] == listing.context["cl"].result_count
    assert parse_qs(urlsplit(navigation["next"]).query)["_changelist_filters"] == [filters]
    assert str(listed_ids[2]) in navigation["next"]
    assert client.get(navigation["next"]).context["project_navigation"]["total"] == 3
    back = client.get(response.context["project_change_url"])
    assert back.context["image_navigation"]["position"] == navigation["position"]
    assert back.context["image_navigation"]["total"] == navigation["total"]
    dashboard_entry = back.context["project_media_dashboard_url"]
    assert parse_qs(urlsplit(dashboard_entry).query)["_changelist_filters"] == [filters]
    model_admin = admin.site._registry[Project]
    listed = next(project for project in listing.context["cl"].queryset if project.pk == current.pk)
    assert "_changelist_filters=" in str(model_admin.media_dashboard_link(listed))
    listed.boundary_review_count = 1
    assert "_changelist_filters=" in str(model_admin.boundary_review_link(listed))


@pytest.mark.parametrize("filters", ["q=nonexistent", "invalid_lookup__exact=true"])
def test_media_dashboard_never_broadens_nonmatching_or_invalid_project_scope(client, records, filters):
    owner, project, _, _ = records
    client.force_login(owner)
    url = reverse("admin:platform_api_project_media_dashboard", args=[project.pk])
    response = client.get(url, {"_changelist_filters": filters})
    assert response.status_code == 200
    assert response.context["project_navigation"] is None
    assert b">Next project &rarr;</a>" not in response.content
    assert b">Previous project</a>" not in response.content


def test_staff_without_model_permission_cannot_navigate(client, records, django_user_model):
    _, _, plans, _ = records
    staff = django_user_model.objects.create_user(username="no-plan-access", is_staff=True)
    client.force_login(staff)
    assert detail(client, "floorplan", plans[0]).status_code == 403


def test_sidebar_quick_queue_link(client, records):
    owner, project, _, _ = records
    client.force_login(owner)
    response = detail(client, "project", project)
    assert response.status_code == 200
    assert b"Quick links" in response.content
    assert reverse("admin:platform_api_project_jobs").encode() in response.content
    assert b"Active queue" in response.content
    assert reverse("admin:platform_api_project_layout_cart").encode() in response.content
    assert b"google_match=incomplete" in response.content
    assert b"maharera=missing" in response.content


def test_media_dashboard_adds_manual_amenities(client, records):
    owner, project, _, _ = records
    client.force_login(owner)
    url = reverse("admin:platform_api_project_media_dashboard", args=[project.pk])

    response = client.get(url, {"section": "amenities"})
    assert response.status_code == 200
    assert b'name="action" value="add_manual_amenities"' in response.content
    assert b"Add missing amenities" in response.content

    response = client.post(url + "?section=amenities", {
        "action": "add_manual_amenities",
        "manual_amenities": "gym, swimming pool",
    })
    assert response.status_code == 302
    assert response.url.endswith("?section=amenities")
    assert set(project.project_amenities.values_list("amenity__taxonomy_code", flat=True)) == {
        "gymnasium", "swimming_pool",
    }

def test_project_delete_confirmation_and_retirement(client, records):
    owner, project, _, _ = records
    client.force_login(owner)
    url = reverse("admin:platform_api_project_delete", args=[project.pk])

    response = client.get(url)
    assert response.status_code == 200
    assert b"Delete project" in response.content

    response = client.post(url, {"confirm": "yes"})
    assert response.status_code == 302
    project.refresh_from_db()
    assert project.status == "retired"

def test_quick_cart_is_current_users_total(client, records, django_user_model):
    from platform_api.models import LayoutCropDraft

    owner, project, plans, _ = records
    other = django_user_model.objects.create_superuser(username="other-cart-owner", password="test-only")
    for actor in (owner, owner, other):
        LayoutCropDraft.objects.create(owner=actor, project=project, source_plan=plans[0],
            source_sha256="0" * 64, label="Private draft", image=b"image", sha256="1" * 64,
            pixel_width=100, pixel_height=100)
    client.force_login(owner)
    response = client.get(reverse("admin:platform_api_project_layout_cart"))
    assert response.status_code == 200
    assert response.context["cart_count"] == 2
    assert len(response.context["cart_projects"]) == 1
    assert "no-store" in response["Cache-Control"]


def extraction_post(plan):
    return {"expected_updated_at": plan.updated_at.isoformat(), "configuration_type": "2BHK",
        "carpet_area_sqft": "650.00", "room_count": "2", "bathroom_count": "2", "balcony_count": "1",
        "orientation": "East", "reason": "Corrected against source image", "rooms-TOTAL_FORMS": "2",
        "rooms-INITIAL_FORMS": "0", "rooms-0-room": "Living", "rooms-0-length_ft": "12.5",
        "rooms-0-width_ft": "10", "rooms-1-room": "Bedroom", "rooms-1-length_ft": "10",
        "rooms-1-width_ft": ""}


def ready_extraction_plan(records):
    owner, project, plans, _ = records
    plan = plans[0]
    plan.document.scan_status = "clean"
    plan.document.save()
    plan.asset.status = "ready"
    plan.asset.save()
    return owner, project, plan


def test_manual_extraction_editor_adds_rooms_without_publishing(client, records):
    from platform_api.models import AuditEvent, ReviewTask, OutboxMessage

    owner, project, plan = ready_extraction_plan(records)
    client.force_login(owner)
    url = reverse("admin:platform_api_floorplan_edit_extraction", args=[plan.pk])
    response = client.get(url)
    assert response.status_code == 200 and b"Add room" in response.content
    assert "no-store" in response["Cache-Control"]
    assert client.post(url, extraction_post(plan)).status_code == 302
    plan.refresh_from_db()
    project.refresh_from_db()
    assert plan.dimensions == [{"room": "Living", "lengthFt": 12.5, "widthFt": 10.0, "areaSqFt": 125},
                               {"room": "Bedroom", "lengthFt": 10.0, "widthFt": None, "areaSqFt": None}]
    assert plan.review_state == "needs_review" and str(plan.carpet_area_sqft) == "650.00"
    assert project.published_fact_set_version == 0 and project.status == "draft"
    task = ReviewTask.objects.get(resolution__source="manual_extraction")
    assert task.resolution["submittedBy"] == owner.pk and task.resolution["reviewState"] == "unreviewed"
    assert AuditEvent.objects.filter(action="floor_plan.extraction_corrected").count() == 1
    assert not OutboxMessage.objects.exists()


@pytest.mark.parametrize("job_scope", ["layout", "document"])
def test_manual_extraction_rejects_stale_invalid_and_pending_work(client, records, job_scope):
    from platform_api.models import OutboxMessage

    owner, _, plan = ready_extraction_plan(records)
    client.force_login(owner)
    url = reverse("admin:platform_api_floorplan_edit_extraction", args=[plan.pk])
    data = extraction_post(plan)
    data["rooms-0-length_ft"] = "NaN"
    assert client.post(url, data).status_code == 400
    data = extraction_post(plan)
    plan.save()
    assert client.post(url, data).status_code == 409
    OutboxMessage.objects.create(
        event_type="floor_plan.crop_evaluation.requested" if job_scope == "layout" else "document.processing.requested",
        correlation_id=uuid4(), payload={"floorPlanId": str(plan.pk)} if job_scope == "layout" else {"documentId": str(plan.document_id)})
    assert client.post(url, extraction_post(plan)).status_code == 409
    plan.refresh_from_db()
    assert plan.dimensions == []


def test_manual_corrections_resolve_only_changed_axis_warnings_and_keep_original_evidence(client, records):
    from .test_room_dimensions import native_room
    from platform_api.domain.room_dimensions import normalize_room_measurements
    owner, _, plan = ready_extraction_plan(records)
    plan.dimensions, metadata = normalize_room_measurements([native_room("Living", 1, 10,
        unitSource="inferred", lengthEstimated=True, widthEstimated=True)])
    plan.raw_extraction = {**metadata, "qualityWarning": "Existing boundary warning"}
    original_readings = metadata["dimensionEvidence"][0]["measurements"]
    plan.save()
    client.force_login(owner)
    url = reverse("admin:platform_api_floorplan_edit_extraction", args=[plan.pk])
    data = extraction_post(plan)
    data.update({"rooms-INITIAL_FORMS": "1", "rooms-0-source_index": "0"})
    assert client.post(url, data).status_code == 302
    plan.refresh_from_db()
    assert "AI-estimated length" not in plan.raw_extraction["dimensionWarning"]
    assert "AI-estimated width" in plan.raw_extraction["dimensionWarning"]
    assert "AI-inferred unit" in plan.raw_extraction["dimensionWarning"]
    assert plan.raw_extraction["dimensionEvidence"][0]["measurements"] == original_readings
    assert plan.raw_extraction["qualityWarning"] == "Existing boundary warning"
    assert plan.review_state == "needs_boundary_review"
    data = extraction_post(plan)
    data.update({"rooms-INITIAL_FORMS": "2", "rooms-0-source_index": "0", "rooms-0-length_ft": "12.5",
        "rooms-0-width_ft": "11", "rooms-1-source_index": "1"})
    assert client.post(url, data).status_code == 302
    plan.refresh_from_db()
    assert plan.raw_extraction["dimensionWarning"] == ""
    assert plan.raw_extraction["dimensionEvidence"][0]["measurements"] == original_readings


def test_manual_extraction_requires_permissions_and_csrf(client, records, django_user_model):
    from django.test import Client

    owner, _, plan = ready_extraction_plan(records)
    url = reverse("admin:platform_api_floorplan_edit_extraction", args=[plan.pk])
    stranger = django_user_model.objects.create_user(username="no-extraction-permission", is_staff=True)
    client.force_login(stranger)
    assert client.get(url).status_code == 403
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(owner)
    assert csrf_client.post(url, extraction_post(plan)).status_code == 403


def test_manual_extraction_fixes_and_removes_existing_rooms(client, records):
    from platform_api.models import ReviewTask

    owner, _, plan = ready_extraction_plan(records)
    plan.dimensions = [{"room": "Living", "lengthFt": 1, "widthFt": 10, "evidence": "source"},
                       {"room": "Duplicate", "lengthFt": 5, "widthFt": 4}]
    plan.raw_extraction = {"sourcePlanName": "Original evidence"}
    plan.display_name = "Saved alias"
    plan.save()
    original_rooms = plan.dimensions
    client.force_login(owner)
    url = reverse("admin:platform_api_floorplan_edit_extraction", args=[plan.pk])
    data = extraction_post(plan)
    data.update({"rooms-INITIAL_FORMS": "2", "rooms-0-source_index": "0", "rooms-1-source_index": "1", "rooms-1-DELETE": "on"})
    assert client.post(url, data).status_code == 302
    plan.refresh_from_db()
    assert plan.dimensions == [{"room": "Living", "lengthFt": 12.5, "widthFt": 10.0, "evidence": "source", "areaSqFt": 125}]
    assert plan.raw_extraction == {"sourcePlanName": "Original evidence"} and plan.display_name == "Saved alias"
    assert ReviewTask.objects.get(resolution__source="manual_extraction").resolution["before"]["dimensions"] == original_rooms
    truncated = extraction_post(plan)
    assert client.post(url, truncated).status_code == 409
    plan.refresh_from_db()
    assert len(plan.dimensions) == 1


@pytest.mark.parametrize("state", ["retired", "removed_by_user"])
def test_manual_extraction_rejects_inactive_sources(client, records, state):
    owner, project, plan = ready_extraction_plan(records)
    if state == "retired":
        project.status = state
        project.save()
    else:
        plan.review_state = state
        plan.save()
    client.force_login(owner)
    assert client.get(reverse("admin:platform_api_floorplan_edit_extraction", args=[plan.pk])).status_code == 404


def test_extraction_browser_room_controls_and_responsive_layout(client, records, tmp_path):
    from io import BytesIO
    from pathlib import Path
    from urllib.parse import urlsplit
    from django.contrib.staticfiles import finders
    from PIL import Image
    from playwright.sync_api import sync_playwright
    import mimetypes

    owner, _, plan = ready_extraction_plan(records)
    client.force_login(owner)
    url = reverse("admin:platform_api_floorplan_edit_extraction", args=[plan.pk])
    html = client.get(url).content
    image = BytesIO()
    Image.new("RGB", (500, 600), "white").save(image, format="PNG")

    def serve(route):
        path = urlsplit(route.request.url).path
        if path.startswith("/static/"):
            filename = finders.find(path.removeprefix("/static/"))
            assert filename, path
            route.fulfill(body=Path(filename).read_bytes(), content_type=mimetypes.guess_type(filename)[0] or "application/octet-stream")
        elif path == url:
            route.fulfill(body=html, content_type="text/html")
        else:
            route.fulfill(body=image.getvalue(), content_type="image/png")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/*", serve)
        page.goto("http://testserver" + url)
        page.get_by_role("button", name="Add room", exact=True).click()
        assert page.locator("#id_rooms-TOTAL_FORMS").input_value() == "2"
        page.locator("#id_rooms-1-room").fill("Kitchen")
        page.locator("#id_rooms-1-length_ft").fill("8")
        page.locator("#id_rooms-1-DELETE").check()
        assert page.locator("#id_rooms-1-DELETE").is_checked()
        for width in (1440, 390, 320):
            page.set_viewport_size({"width": width, "height": 1000})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), width
            page.screenshot(path=str(tmp_path / f"extraction-{width}.png"), full_page=True)
        assert not errors
        browser.close()


def test_quick_links_respect_queue_and_cart_permissions(client, records, django_user_model):
    from django.contrib.auth.models import Permission

    _, project, _, _ = records
    viewer = django_user_model.objects.create_user(username="project-only-viewer", is_staff=True)
    viewer.user_permissions.add(Permission.objects.get(content_type__app_label="platform_api", codename="view_project"))
    client.force_login(viewer)
    response = detail(client, "project", project)
    assert response.status_code == 200 and b"Maps: incomplete data" in response.content
    assert b">Active queue</a>" not in response.content and b">My active cart</a>" not in response.content
    assert client.get(reverse("admin:platform_api_project_layout_cart")).status_code == 403
    assert client.get(reverse("admin:platform_api_project_jobs")).status_code == 403


@pytest.mark.parametrize("actor", ["guest", "buyer", "reviewer"])
def test_project_gallery_permissions_and_direct_images(client, records, django_user_model, monkeypatch, actor):
    from io import BytesIO
    from platform_api import views

    owner, project, plans, assets = records
    project.status = "published"
    project.save()
    document = plans[0].document
    document.scan_status = "clean"
    document.save()
    plans[0].review_state = "approved"
    plans[0].dimensions = [{"room": "Living", "lengthFt": 12, "widthFt": 10, "privateEvidence": "do not disclose"}]
    plans[0].raw_extraction = {"private": "not for catalogue"}
    plans[0].save()
    plans[2].active = False
    plans[2].save()
    if actor != "guest":
        client.force_login(owner if actor == "reviewer" else django_user_model.objects.create_user(username="gallery-buyer"))
    monkeypatch.setattr(views.default_storage, "exists", lambda key: True)
    monkeypatch.setattr(views.default_storage, "open", lambda *args: BytesIO(b"test-image"))
    url = reverse("project-gallery", args=[project.pk])
    response = client.get(url)
    assert response.status_code == 200
    body = response.json()
    assert len(body["results"]) == (2 if actor == "reviewer" else 1)
    assert body["access"] == ("review" if actor == "reviewer" else "published")
    assert "privateEvidence" not in response.content.decode() and "objectKey" not in response.content.decode()
    assert "rawExtraction" not in response.content.decode() and "documentId" not in response.content.decode()
    assert body["results"][0]["layouts"][0]["rooms"] == [{"room": "Living", "lengthFt": 12, "widthFt": 10, "areaSqFt": 120}]
    assert body["results"][0]["layouts"][0]["roomRectangleSumSqFt"] == 120
    assert client.get(body["results"][0]["url"]).status_code == 200
    assert client.get(reverse("floor-plan-asset-media", args=[assets[1].pk])).status_code == (200 if actor == "reviewer" else 404)
    assert client.get(reverse("floor-plan-asset-media", args=[assets[2].pk])).status_code == (200 if actor == "reviewer" else 404)
    project.status = "ingestion"
    project.save()
    processing = client.get(url).json()
    assert processing["access"] == ("review" if actor == "reviewer" else "restricted")
    assert len(processing["results"]) == (2 if actor == "reviewer" else 0)
    assert client.get(reverse("floor-plan-asset-media", args=[assets[0].pk])).status_code == (200 if actor == "reviewer" else 404)
    project.status = "retired"
    project.save()
    assert client.get(url).status_code == 404
    if actor != "reviewer":
        assert client.get(reverse("floor-plan-asset-media", args=[assets[0].pk])).status_code == 404


@pytest.mark.parametrize("can_review", [False, True])
def test_project_gallery_links_only_eligible_derived_layouts_across_pages(client, records, monkeypatch, can_review):
    from platform_api.views import BuyerCursorPagination

    owner, project, plans, assets = records
    project.status = "published"
    project.save()
    document = plans[0].document
    document.scan_status = "clean"
    document.save()
    for plan in plans[:2]:
        plan.review_state = "approved"
        plan.save()
    # Two distinct floor-plan images on the same page must not share their units.
    assets[1].page_number = plans[1].page_number = 1
    assets[1].save()
    plans[1].save()

    def unit(source, *, state="approved", active=True, raw_source=False):
        asset = FloorPlanAsset.objects.create(
            project=project, document=document, workflow_run=plans[0].workflow_run,
            page_number=source.page_number, asset_type="unit_crop", object_key=f"derived/{uuid4()}.png",
            byte_length=1, sha256=uuid4().hex * 2, pixel_width=100, pixel_height=100,
            detector_version="test", source_bounds={"category": "layouts", **(
                {} if raw_source else {"sourceAssetId": str(source.pk)}
            )},
        )
        return FloorPlan.objects.create(
            project=project, document=document, workflow_run=plans[0].workflow_run,
            asset=asset, page_number=asset.page_number, plan_scope="flat",
            plan_name=str(uuid4()), display_name="Shared alias", review_state=state, active=active,
            raw_extraction={"boundingBox": {"sourceAssetId": str(source.pk)}} if raw_source else {},
        )

    first = unit(assets[0])
    second = unit(assets[1])
    historical = unit(assets[0], state="replaced_by_user")
    recropped = unit(historical.asset, raw_source=True)
    pending = unit(assets[0], state="needs_review")
    unit(assets[0], state="rejected")
    unit(assets[0], active=False)
    bad_page = unit(assets[0])
    bad_page.page_number = 3
    bad_page.asset.page_number = 3
    bad_page.asset.save()
    bad_page.save()
    if can_review:
        client.force_login(owner)
    monkeypatch.setattr(BuyerCursorPagination, "page_size", 1)
    next_url = reverse("project-gallery", args=[project.pk])
    items = {}
    while next_url:
        response = client.get(next_url)
        assert response.status_code == 200
        body = response.json()
        items.update({item["id"]: item for item in body["results"]})
        next_url = body["next"]
    expected = {str(first.pk), str(recropped.pk)}
    if can_review:
        expected.add(str(pending.pk))
    assert set(items[str(assets[0].pk)]["layoutIds"]) == expected
    assert items[str(assets[1].pk)]["layoutIds"] == [str(second.pk)]
    assert "sourceAssetId" not in str(items) and "documentId" not in str(items)


def test_project_gallery_pagination_and_scan_guards(client, records, monkeypatch):
    from platform_api.views import BuyerCursorPagination

    owner, project, plans, assets = records
    project.status = "needs_review"
    project.save()
    document = plans[0].document
    document.scan_status = "clean"
    document.save()
    client.force_login(owner)
    monkeypatch.setattr(BuyerCursorPagination, "page_size", 1)
    next_url = reverse("project-gallery", args=[project.pk])
    seen = []
    while next_url:
        response = client.get(next_url)
        assert response.status_code == 200
        body = response.json()
        seen.extend(item["id"] for item in body["results"])
        assert len(seen) <= 3
        next_url = body["next"]
    assert set(seen) == {str(asset.pk) for asset in assets}
    document.scan_status = "quarantined"
    document.save()
    assert client.get(reverse("project-gallery", args=[project.pk])).json()["results"] == []


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("project_status", ["ingestion", "published"])
def test_project_gallery_temporary_public_preview(client, records, settings, monkeypatch, enabled, project_status):
    from io import BytesIO
    from platform_api import views
    from platform_api.models import ProjectFact

    settings.PROJECT_PUBLIC_PREVIEW_ENABLED = enabled
    owner, project, plans, assets = records
    project.status = project_status
    project.address = {"privateInternalMetadata": "must not escape"}
    project.save()
    document = plans[0].document
    document.scan_status = "clean"
    document.save()
    ProjectFact.objects.create(project=project, field_code="project.towers", typed_value=3,
        source_class="developer", fact_set_version=1, status="draft", valid_from=timezone.now())
    plans[2].review_state = "removed_by_user"
    plans[2].save()
    monkeypatch.setattr(views.default_storage, "exists", lambda key: True)
    monkeypatch.setattr(views.default_storage, "open", lambda *args: BytesIO(b"test-image"))
    response = client.get(reverse("project-gallery", args=[project.pk]))
    assert response.status_code == 200
    body = response.json()
    assert body["access"] == ("preview" if enabled else "published" if project_status == "published" else "restricted")
    assert len(body["results"]) == (2 if enabled else 0)
    assert "privateInternalMetadata" not in response.content.decode()
    assert client.get(reverse("floor-plan-asset-media", args=[assets[0].pk])).status_code == (200 if enabled else 404)
    assert client.get(reverse("floor-plan-asset-media", args=[assets[2].pk])).status_code == 404
    detail = client.get(reverse("project-detail", args=[project.pk]))
    assert detail.status_code == (200 if enabled or project_status == "published" else 404)
    if enabled:
        assert detail.json()["facts"][0]["value"] == 3
        assert "evidence" not in detail.json()["facts"][0] and "address" not in detail.json()
        assert response["Cache-Control"] == "private, no-store"
        if project_status != "published":
            assert body["project"]["facts"][0]["status"] == "draft"
    assert client.get(f"/api/v1/developer-projects/{project.pk}/documents").status_code in {401, 403}
    assert client.get("/api/v1/requirement-profiles/").status_code in {401, 403}
    assert client.post(f"/api/v1/review/projects/{project.pk}:publish", {}).status_code in {401, 403}
    settings.PROJECT_PUBLIC_PREVIEW_ENABLED = False
    assert client.get(reverse("floor-plan-asset-media", args=[assets[0].pk])).status_code == 404


def test_external_sources_navigation_preserves_quick_link_queue(client, records):
    owner, project, _, _ = records
    following = Project.objects.create(organization=project.organization, submitted_name="Z next")
    Project.objects.create(organization=project.organization, submitted_name="Other", maha_rera_id="P12345678901")
    client.force_login(owner)
    filters = "maharera=missing"
    listing = client.get(reverse("admin:platform_api_project_changelist"), {"maharera": "missing"})
    assert b"_changelist_filters=maharera%3Dmissing" in listing.content
    url = reverse("admin:platform_api_project_external_sources", args=[project.pk]) + "?" + urlencode({"_changelist_filters": filters})
    response = client.get(url)
    nav = response.context["image_navigation"]
    assert nav["total"] == 2 and nav["previous"] is None
    assert str(following.pk) in nav["next"] and "/external-sources/" in nav["next"]
    assert parse_qs(urlsplit(nav["next"]).query)["_changelist_filters"] == [filters]
    assert b"Quick-link record navigation" in response.content
    assert client.get(nav["next"]).context["image_navigation"]["next"] is None
    preview = client.post(url, {"action": "direct_location", "latitude": "19.15", "longitude": "72.95"})
    assert parse_qs(urlsplit(preview.url).query)["_changelist_filters"] == [filters]
    assert client.get(preview.url).context["image_navigation"]["total"] == 2
    invalid = client.get(reverse("admin:platform_api_project_external_sources", args=[project.pk]), {"_changelist_filters": "q=absent"})
    assert invalid.context["image_navigation"] is None


def test_map_preview_browser_layout(client, records, tmp_path):
    from pathlib import Path
    import mimetypes
    from django.contrib.staticfiles import finders
    from playwright.sync_api import expect, sync_playwright

    owner, project, _, _ = records
    client.force_login(owner)
    url = reverse("admin:platform_api_project_external_sources", args=[project.pk])
    preview = client.post(url, {"action": "direct_location", "latitude": "19.1192391", "longitude": "72.9371398"})
    response = client.get(preview.url)

    def serve(route):
        path = urlsplit(route.request.url).path
        if path.startswith("/static/"):
            filename = finders.find(path.removeprefix("/static/"))
            assert filename, path
            route.fulfill(body=Path(filename).read_bytes(), content_type=mimetypes.guess_type(filename)[0] or "application/octet-stream")
        else:
            route.fulfill(body=response.content, content_type="text/html", headers={
                "Content-Security-Policy": response["Content-Security-Policy"], "Referrer-Policy": response["Referrer-Policy"]})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.route("http://testserver/**", serve)
        page.goto("http://testserver" + preview.url)
        frame = page.locator('iframe[title="Draft project location map"]')
        frame.scroll_into_view_if_needed()
        expect(frame).to_be_visible()
        expect(page.frame_locator("iframe").locator(".maplibregl-marker")).to_be_visible(timeout=20000)
        expect(page.frame_locator("iframe").locator(".maplibregl-canvas")).to_be_visible(timeout=20000)
        section = page.locator('section[aria-labelledby="draft-heading"]')
        for width in (1440, 390, 320):
            page.set_viewport_size({"width": width, "height": 1000})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), width
            bounds = frame.bounding_box()
            assert bounds and bounds["width"] <= min(600, width)
            assert bounds["height"] == pytest.approx(max(260, bounds["width"] * 9 / 16), abs=1)
            section.screenshot(path=str(tmp_path / f"map-preview-{width}.png"))
        assert not page.get_by_role("checkbox", name="I confirm these references belong to this project and submit them for review.").is_checked()
        print(f"Map preview screenshots: {tmp_path}")
        browser.close()
