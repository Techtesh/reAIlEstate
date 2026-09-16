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
def test_project_admin_columns_preserve_fields_and_links(client, django_user_model, adding):
    from django.urls import reverse

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
        "organization", "status", "maha_rera_id", "submitted_name", "address", "locality", "latitude", "longitude",
        "published_fact_set_version", "processing_attested", "verification_status", "verification_checked_at",
    ]
    assert b"field-extraction_outputs" in response.content and b"field-media_dashboard_link" in response.content
    assert b'name="_save"' in response.content
    if not adding:
        assert reverse("admin:platform_api_project_media_dashboard", args=[project.pk]).encode() in response.content
    assert Project.objects.values().get(pk=project.pk) == before


@pytest.mark.django_db
def test_project_admin_columns_preserve_validation_and_saving(client, django_user_model):
    from django.urls import reverse

    actor = django_user_model.objects.create_superuser(username="project-form-save", password="test-only")
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Form saves"),
                                     submitted_name="Original name")
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