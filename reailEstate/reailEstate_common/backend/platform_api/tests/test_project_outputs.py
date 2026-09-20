import hashlib
import io
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.core.files.base import ContentFile
from django.utils import timezone
from rest_framework.test import APIClient

from platform_api.artifact_storage import storage
from platform_api.document_formats import DOCX, prepare_document
from platform_api.models import EvidenceDocument, Organization, OrganizationMembership, Project
from platform_api.project_outputs import build_project_output, extract_fields, layout_metrics, materialize_project_amenities
from platform_api.rag import SourcePage
from platform_api.workers import detect_media_type, process_document, enqueue_document
from .test_floor_plan_layout_jobs import source


TEXT = """Project name: Test Gardens
Location: Thane, Maharashtra
Developer: Example Homes
MahaRERA: P51700053528
Total land holding: 2 acres
Total towers: 3
Total residential units: 200
Total parkings: 220
Tower A: 80 residential units, 2 lifts
Tower B: 4 flats per floor, 3 lifts
Nearest metro station: Test Metro
Nearest local station: Thane
Price: INR 1.2 crore
Offer: Registration fee waiver
Possession: December 2028
Amenities: garden, gymnasium and swimming pool
"""


def stub_run():
    return SimpleNamespace(id=uuid4(), project_id=uuid4(), document_id=uuid4(), project=SimpleNamespace(submitted_name="Test Gardens"))


@pytest.mark.django_db
@pytest.mark.parametrize("adding", [False, True])
def test_project_admin_columns_preserve_fields_and_links(client, django_user_model, adding, settings):
    from django.urls import clear_url_caches, reverse

    settings.ROOT_URLCONF = "admin_service.urls"
    clear_url_caches()

    actor = django_user_model.objects.create_superuser(username="project-form-editor", password="test-only")
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Form tests"),
                                     submitted_name="Test Gardens")
    client.force_login(actor)
    url = reverse("admin:platform_api_project_add") if adding else reverse("admin:platform_api_project_change", args=[project.pk])
    before = Project.objects.values().get(pk=project.pk)
    response = client.get(url)
    assert response.status_code == 200
    assert b"project-details-grid" in response.content and b"platform_api/project_form.css" in response.content
    assert list(response.context["adminform"].form.fields) == [
        "organization", "status", "maha_rera_id", "submitted_name",
        "published_fact_set_version", "verification_status", "processing_attested", "verification_checked_at",
        "address", "locality",
    ]
    assert b'name="latitude"' not in response.content and b'name="longitude"' not in response.content
    assert b'project-details-layout' in response.content
    assert b"field-extraction_outputs" in response.content and b"field-media_dashboard_link" in response.content
    assert b"field-google_location_reference" in response.content
    from django import forms
    assert isinstance(response.context["adminform"].form.fields["status"].widget, forms.Select)
    assert isinstance(response.context["adminform"].form.fields["verification_status"].widget, forms.Select)
    assert b'name="project_pic"' not in response.content
    assert b'name="project_picture_source"' not in response.content
    assert b'name="_save"' in response.content
    assert b'class="project-address-disclosure">' in response.content
    assert b"Layouts, prices and sizes" in response.content
    if adding:
        assert b"Save the project to add layouts" in response.content
    else:
        assert b"No active layouts available." in response.content
    if not adding:
        assert reverse("admin:platform_api_project_media_dashboard", args=[project.pk]).encode() in response.content
    assert Project.objects.values().get(pk=project.pk) == before


@pytest.mark.django_db
def test_project_admin_columns_preserve_validation_and_saving(client, django_user_model):
    from django.urls import reverse

    actor = django_user_model.objects.create_superuser(username="project-form-save", password="test-only")
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Form saves"),
                                     submitted_name="Original name", latitude="19.150000", longitude="72.950000")
    client.force_login(actor)
    url = reverse("admin:platform_api_project_change", args=[project.pk])
    data = {"organization": str(project.organization_id), "status": project.status, "submitted_name": "",
            "address": json.dumps({"city": "Thane"}), "locality": "Thane", "published_fact_set_version": 0,
            "verification_status": project.verification_status, "_save": "Save"}
    response = client.post(url, data)
    assert response.status_code == 200
    assert "submitted_name" in response.context["adminform"].form.errors
    assert b"project-details-grid" in response.content
    project.refresh_from_db()
    assert project.submitted_name == "Original name"
    response = client.post(url, dict(data, submitted_name="Updated name"))
    assert response.status_code == 302, response.context["adminform"].form.errors
    project.refresh_from_db()
    assert project.submitted_name == "Updated name" and project.locality == "Thane"
    assert float(project.latitude) == 19.15 and float(project.longitude) == 72.95
    response = client.post(url, dict(data, submitted_name="Updated name", address="{invalid json"))
    assert response.status_code == 200
    assert "address" in response.context["adminform"].form.errors
    assert b'class="project-address-disclosure" open>' in response.content
    project.refresh_from_db()
    assert project.address == {"city": "Thane"}


@pytest.mark.django_db
def test_project_change_transit_maps_are_scoped_and_escaped(client, django_user_model, monkeypatch):
    from django.urls import reverse
    from platform_api import external_sources_admin as sources

    actor = django_user_model.objects.create_superuser(username="transit-map-reader", password="test-only")
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Transit tests"),
        submitted_name="Test project", latitude="19.1", longitude="72.9")
    monkeypatch.setattr(sources, "saved_station_routes", lambda project: {"groups": [{"stations": [
        {"kind": "metro", "name": "<Station>", "serviceStatus": "planned",
         "location": {"latitude": 19.2, "longitude": 72.9},
         "walking": {"status": "available", "distanceMeters": 500}}]}]})
    client.force_login(actor)
    response = client.get(reverse("admin:platform_api_project_change", args=[project.pk]))
    assert response.status_code == 200
    html = response.content.decode()
    assert "&lt;Station&gt;" in html and "<Station>" not in html
    assert "Upcoming: planned" in html and "No saved railway walking route" in html
    assert 'id="transit-metro"' in html and 'id="transit-railway"' in html
    assert 'title="Walking directions to &lt;Station&gt;"' in html
    assert "frame-src https://maps.google.com https://www.google.com" in response["Content-Security-Policy"]
    assert "frame-src" not in client.get(reverse("admin:platform_api_project_changelist"))["Content-Security-Policy"]
    assert b"project-transit-heading" not in client.get(reverse("admin:platform_api_project_add")).content


@pytest.mark.django_db
def test_project_details_responsive_layout(client, source, monkeypatch, tmp_path):
    import mimetypes
    from pathlib import Path
    from urllib.parse import urlsplit
    from django.contrib.staticfiles import finders
    from django.urls import reverse
    from platform_api import external_sources_admin as sources
    from platform_api.models import FloorPlan

    playwright_api = pytest.importorskip("playwright.sync_api")
    plan, actor = source
    project = plan.project
    project.latitude, project.longitude = "19.1", "72.9"
    project.save()
    for index in range(12):
        FloorPlan.objects.create(
            project=project, document=plan.document, workflow_run=plan.workflow_run,
            page_number=2, plan_scope="flat", plan_name=f"Layout {index} with a longer name",
            carpet_area_sqft=700 + index, price=15000000 + index,
            extractor_version="test",
        )
    monkeypatch.setattr(sources, "saved_station_routes", lambda project: {"groups": [{"stations": [
        {"kind": kind, "name": f"Selected {kind} station", "serviceStatus": "unverified",
         "location": {"latitude": 19.2, "longitude": 72.95},
         "walking": {"status": "available", "distanceMeters": 1200}} for kind in ("metro", "railway")]}]})
    client.force_login(actor)
    url = reverse("admin:platform_api_project_change", args=[project.pk])
    response = client.get(url)

    def serve(route):
        path = urlsplit(route.request.url).path
        if path == url:
            route.fulfill(body=response.content, content_type="text/html")
        elif path.startswith("/static/"):
            filename = finders.find(path.removeprefix("/static/"))
            if filename:
                route.fulfill(body=Path(filename).read_bytes(), content_type=mimetypes.guess_type(filename)[0] or "application/octet-stream")
            else:
                route.abort()
        else:
            route.abort()

    with playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.route("**/*", serve)
            page.goto("http://testserver" + url)
            for width in (1440, 900, 390, 320):
                page.set_viewport_size({"width": width, "height": 1000})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
                metadata = page.locator(".project-metadata").bounding_box()
                resources = page.locator(".project-resources").bounding_box()
                transit = page.locator(".project-transit").bounding_box()
                address = page.locator(".project-address-disclosure").bounding_box()
                layouts = page.locator(".project-layout-summary").bounding_box()
                assert metadata and resources and transit and address and layouts
                assert not page.locator("#id_address").is_visible()
                assert page.locator(".project-layout-table tbody tr").count() == 12
                assert page.locator(".project-layout-scroll").evaluate("element => element.scrollHeight > element.clientHeight")
                assert layouts["y"] >= resources["y"] + resources["height"] - 1
                assert address["y"] >= layouts["y"] + layouts["height"] - 1
                if width > 900:
                    assert transit["x"] > resources["x"]
                    assert abs(transit["y"] - resources["y"]) < 2
                    assert transit["width"] > resources["width"]
                else:
                    assert transit["y"] >= address["y"] + address["height"] - 1
                page.locator(".project-address-disclosure > summary").click()
                assert page.locator("#id_address").is_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
                page.locator(".project-address-disclosure > summary").click()
                assert page.locator(".project-transit .project-transit-tools").count() == 1
                assert page.locator("#id_latitude,#id_longitude").count() == 0
                page.locator('label[for="transit-railway"]').click()
                assert page.locator("#transit-panel-railway").is_visible()
                assert not page.locator("#transit-panel-metro").is_visible()
                page.locator('label[for="transit-metro"]').click()
                page.locator(".project-details-layout").screenshot(path=str(tmp_path / f"project-details-{width}.png"))
            print(f"Project layout screenshots: {tmp_path}")
        finally:
            browser.close()


def test_structured_fields_and_units_preserve_unknown_enrichment(settings):
    output = build_project_output(stub_run(), [SourcePage(1, TEXT, {})], [])
    assert output["name"]["value"] == "Test Gardens"
    assert output["location"]["value"] == "Thane, Maharashtra"
    assert output["offeredPriceInr"]["value"] == 12_000_000
    assert output["offers"]["value"] == "Registration fee waiver"
    parcel = output["parcelDetails"]
    assert parcel["landHoldingSqFt"]["value"] == 87120
    assert parcel["effectiveParcel"]["value"] == 435.6
    assert parcel["totalTowers"]["validationStatus"] == "unavailable"
    assert parcel["towerUnitsAndLifts"][0]["residentialUnits"] == 80
    assert parcel["towerUnitsAndLifts"][1]["residentialUnits"] is None
    assert parcel["towerUnitsAndLifts"][1]["unitsPerFloor"] == 4
    assert output["nearestMetro"]["walkingMinutes"] is None
    assert output["nearestMetro"]["documentClaim"]["value"] == "Test Metro"
    assert output["rera"]["registrations"][0]["projectUrl"] is None
    assert output["developer"]["socialSentiment"]["status"] == "unavailable"
    assert output["amenities"]["nature"]["values"] == ["garden"]


def test_highland_haven_amenity_labels_are_recognized():
    text = """Landscape Garden, Jogging track, Senior Citizen Area, Pergola, Climber,
Amphitheater, Multipurpose court, Kids play area, Yoga deck, Multipurpose lawn,
Baby Creche, Swimming pool, Fully Equipped Gym, Library and Reading area,
Steam room, Multipurpose room, Guest Room, Indoor Game"""
    output = build_project_output(stub_run(), [SourcePage(7, text, {})], [])
    values = {value for section in output["amenities"].values() for value in section["values"]}
    assert values == {
        "amphitheatre", "children_play_area", "climber", "creche", "garden", "guest_room",
        "gymnasium", "indoor_games", "jogging_track", "library_reading_area", "multipurpose_court",
        "multipurpose_lawn", "multipurpose_room", "pergola", "senior_citizen_area", "steam_room",
        "swimming_pool", "yoga_deck",
    }


def test_conflicting_totals_do_not_generate_effective_parcel():
    output = build_project_output(stub_run(), [SourcePage(1, TEXT, {}), SourcePage(2, "Total residential units: 300", {})], [])
    assert output["parcelDetails"]["totalResidentialUnits"]["status"] == "conflict"
    assert output["parcelDetails"]["effectiveParcel"]["value"] is None


def test_registrations_and_station_provider_provenance(monkeypatch):
    class Provider:
        def registration(self, number):
            return {"sourceUrl": "https://maharera.maharashtra.gov.in/project/approved-record", "retrievedAt": "2026-09-08T00:00:00Z",
                    "data": {"registrationNumber": number, "totalTowers": 3, "totalResidentialUnits": 250}}

        def stations(self, location):
            return {"sourceUrl": "https://maps.example.com/record", "retrievedAt": "2026-09-08T00:00:00Z",
                    "data": {"metro": {"name": "Test Metro", "isNearestVerified": True, "walkingMinutes": 12, "drivingMinutes": 4}}}

    monkeypatch.setattr("platform_api.project_outputs.backend", Provider)
    result = build_project_output(stub_run(), [SourcePage(1, TEXT, {})], [])
    assert result["parcelDetails"]["totalTowers"]["validationStatus"] == "matched"
    assert result["parcelDetails"]["totalResidentialUnits"]["validationStatus"] == "mismatch"
    assert result["nearestMetro"]["walkingMinutes"] == 12
    assert "travelmode=walking" in result["nearestMetro"]["walkingDirectionsUri"]
    assert result["nearestLocalStation"]["status"] == "unavailable"


def test_rectangle_metrics_do_not_invent_scores():
    plan = SimpleNamespace(id=uuid4(), page_number=2, plan_scope="flat", configuration_type="2BHK", carpet_area_sqft=720,
        display_name="Garden home", display_title="Garden home",
        dimensions=[{"room": "Master Bedroom", "lengthFt": 10.5, "widthFt": 12}, {"room": "Bedroom 2", "lengthFt": 10, "widthFt": 11}],
        review_state="needs_review", extractor_version="test", asset_id=None)
    metrics = layout_metrics(plan, {"effectiveParcel": {"value": 400}})
    assert metrics["Master_Bedroom_sqft_adj"] == 126
    assert metrics["Bedroom2_sqft_adj"] == 110
    assert metrics["roomRectangleSumSqFt"] == 236
    assert metrics["Unused_Space"] is None
    assert metrics["Score_Total"] is None
    assert metrics["PSF"] is None


@pytest.mark.django_db
def test_materialize_project_amenities_uses_only_recognized_taxonomy():
    project = Project.objects.create(
        organization=Organization.objects.create(legal_name="Amenity Builder"),
        submitted_name="Amenity Project",
    )
    output = {"amenities": {"social": {
        "values": ["clubhouse", "unreviewed model prose"],
        "evidence": [
            {"value": "clubhouse", "documentId": "source", "pageNumber": 7},
            {"value": "unreviewed model prose", "documentId": "source", "pageNumber": 8},
        ],
    }}}

    materialized = materialize_project_amenities(project, output)

    assert [item.amenity.taxonomy_code for item in materialized] == ["clubhouse"]
    assert materialized[0].evidence_summary["reviewState"] == "unreviewed"


def test_legacy_office_requires_explicit_converter(settings):
    settings.OFFICE_CONVERTER_ENABLED = False
    with pytest.raises(ValueError, match="OFFICE_CONVERTER_REQUIRED"):
        prepare_document(b"legacy", "application/msword")
    assert prepare_document(b"native", DOCX) == (b"native", DOCX, False)
    assert detect_media_type(io.BytesIO(b"{\\rtf1 document}"), "application/rtf") == "application/rtf"
    assert detect_media_type(io.BytesIO(b"not a doc"), "application/msword") == "application/octet-stream"


@pytest.mark.django_db
def test_processing_outputs_and_transactional_move(settings, tmp_path, django_user_model, django_capture_on_commit_callbacks):
    settings.MEDIA_ROOT = tmp_path / "legacy"
    settings.FLOOR_PLAN_EXTRACTION_MODE = "script"
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.SECURE_SSL_REDIRECT = False
    user = django_user_model.objects.create_user(username="output-owner")
    org = Organization.objects.create(legal_name="Output Builder")
    OrganizationMembership.objects.create(organization=org, user=user, role="owner")
    project = Project.objects.create(organization=org, submitted_name="Test Gardens")
    content = TEXT.encode()
    key = storage.save(f"user/media/todo/{project.id}/{uuid4()}.txt", ContentFile(content))
    document = EvidenceDocument.objects.create(project=project, uploaded_by=user, original_name="brochure.txt",
        object_key=key, declared_media_type="text/plain", byte_length=len(content), sha256=hashlib.sha256(content).hexdigest(),
        status="quarantined", retention_until=timezone.now() + timedelta(days=7))
    scanner = SimpleNamespace(scan=lambda source: "clean")
    with django_capture_on_commit_callbacks(execute=True):
        processed = process_document(document.id, scanner=scanner)
        assert storage.exists(key)  # Old copy retained until commit.
    assert processed.status == "needs_review"
    assert processed.object_key.startswith("user/media/completed/")
    assert not storage.exists(key)
    run = processed.ingestion_runs.get()
    assert run.status == "completed"
    output_key = run.summary["outputs"]["project"]["objectKey"]
    with storage.open(output_key) as data:
        assert json.load(data)["name"]["value"] == "Test Gardens"
    client = APIClient()
    client.force_authenticate(user)
    response = client.get(f"/api/v1/developer-projects/{project.id}/extraction-output")
    assert response.status_code == 200
    assert "objectKey" not in response.content.decode()
    download = client.get(run.summary["outputs"]["layout-metrics"]["uri"])
    assert download.status_code == 200
    download.close()
    stranger = django_user_model.objects.create_user(username="output-stranger")
    client.force_authenticate(stranger)
    assert client.get(run.summary["outputs"]["project"]["uri"]).status_code == 403
    with django_capture_on_commit_callbacks(execute=True):
        enqueue_document(processed, uuid4())
    assert processed.object_key.startswith("user/media/todo/")
    assert storage.exists(processed.object_key)


@pytest.mark.django_db
def test_rejected_upload_does_not_move_to_completed(settings, django_user_model):
    user = django_user_model.objects.create_user(username="bad-upload")
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Bad"), submitted_name="Bad")
    content = b"text"
    key = storage.save(f"user/media/todo/{project.id}/bad.txt", ContentFile(content))
    doc = EvidenceDocument.objects.create(project=project, uploaded_by=user, object_key=key, original_name="bad.txt",
        declared_media_type="text/plain", byte_length=4, sha256="0" * 64, status="quarantined", retention_until=timezone.now())
    result = process_document(doc.id, scanner=SimpleNamespace(scan=lambda source: "clean"))
    assert result.status == "rejected"
    assert result.error_category == "SOURCE_INTEGRITY_MISMATCH"
    assert result.object_key == key
    assert storage.exists(key)