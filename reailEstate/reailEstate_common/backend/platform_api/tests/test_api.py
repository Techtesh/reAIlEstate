from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APIClient
from .test_layout_pricing import layout, source

from platform_api.models import Consent, RequirementProfile


@pytest.mark.django_db(transaction=True)
def test_project_picture_upload_visibility_and_clear(settings, tmp_path):
    from io import BytesIO
    from PIL import Image
    from django.core.files.uploadedfile import SimpleUploadedFile
    from platform_api.project_pictures import normalize_picture
    from platform_api.models import Organization, Project
    from platform_api.serializers import ProjectListSerializer

    settings.MEDIA_ROOT = tmp_path
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Pictures"),
                                     submitted_name="Project with picture", status="published")
    assert ProjectListSerializer(project).data["project_pic"] is None
    image = BytesIO()
    Image.new("RGB", (100, 60), "green").save(image, format="JPEG")
    from django.core.files.base import ContentFile
    picture_bytes = normalize_picture(image.getvalue())
    assert picture_bytes.startswith(b"\x89PNG")
    picture = ContentFile(picture_bytes, name="project.png")
    project.project_pic = picture
    project.save()
    path = f"/api/v1/projects/{project.pk}/picture"
    assert ProjectListSerializer(project).data["project_pic"].startswith(path)
    guest = APIClient()
    for state, expected in [("published", 200), ("ingestion", 200), ("needs_review", 200), ("draft", 404), ("withdrawn", 404), ("retired", 404), ("merged", 404)]:
        project.status = state
        project.save()
        response = guest.get(path)
        assert response.status_code == expected
        response.close()
    project.status = "ingestion"
    project.save()
    data = guest.get("/api/v1/projects/?includeProcessing=true").json()["results"][0]
    assert data["projectPic"].startswith(path)
    project.address = {"mergedIntoProjectId": "other"}
    project.save()
    assert guest.get(path).status_code == 404
    project.project_pic = ""
    project.save()
    assert ProjectListSerializer(project).data["project_pic"] is None
    assert guest.get(path).status_code == 404



@pytest.fixture
def user(db):
    return User.objects.create_user(username="buyer", password="safe-test-password")


@pytest.mark.django_db(transaction=True)
def test_project_picture_document_selection_and_default(settings, tmp_path, user, monkeypatch):
    import hashlib
    from io import BytesIO
    from PIL import Image
    from django.core.files.base import ContentFile
    from platform_api.artifact_storage import storage
    from platform_api.models import AgentWorkflowRun, EvidenceDocument, FloorPlanAsset, IngestionRun, Organization, Project
    from platform_api import project_pictures

    settings.MEDIA_ROOT = tmp_path
    settings.PROJECT_PUBLIC_PREVIEW_ENABLED = True
    project = Project.objects.create(organization=Organization.objects.create(legal_name="Picture choices"),
                                     submitted_name="Picture choices", status="ingestion")
    document = EvidenceDocument.objects.create(project=project, uploaded_by=user, original_name="brochure.pdf",
        object_key="brochure.pdf", declared_media_type="application/pdf", byte_length=1, sha256="test-source",
        scan_status="clean", retention_until=timezone.now() + timedelta(days=30))
    run = IngestionRun.objects.create(project=project, document=document)
    workflow = AgentWorkflowRun.objects.create(project=project, document=document, ingestion_run=run)
    image = BytesIO()
    Image.new("RGB", (100, 60), "green").save(image, format="PNG")
    content = image.getvalue()
    asset = FloorPlanAsset.objects.create(project=project, document=document, workflow_run=workflow, page_number=2,
        object_key=storage.save("extracted.png", ContentFile(content)), byte_length=len(content),
        sha256=hashlib.sha256(content).hexdigest(), pixel_width=100, pixel_height=60, detector_version="test")
    project.project_pic = project_pictures.extracted_picture(project, asset.pk)
    project.save()
    project.refresh_from_db()
    assert project.project_pic
    with project.project_pic.open("rb") as picture:
        assert picture.read() == content
    assert list(project_pictures.picture_candidates(project)) == [asset]
    assert storage.exists(asset.object_key)
    other = Project.objects.create(organization=project.organization, submitted_name="Other project")
    with pytest.raises(ValueError, match="no longer available"):
        project_pictures.extracted_picture(other, asset.pk)
    asset.sha256 = "incorrect"
    asset.save()
    with pytest.raises(ValueError, match="checksum"):
        project_pictures.extracted_picture(project, asset.pk)
    project.project_pic = ""
    project.save()
    project.refresh_from_db()
    assert not project.project_pic
    rendered = []

    def render_first_page(source, pages):
        rendered.append((source.pk, pages))
        return [(1, content)]

    monkeypatch.setattr(project_pictures, "render_source_pages", render_first_page)
    guest = APIClient()
    path = f"/api/v1/projects/{project.pk}/picture"
    response = guest.get(path)
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == content
    response.close()
    assert rendered == [(document.pk, [1])]
    settings.PROJECT_PUBLIC_PREVIEW_ENABLED = False
    assert guest.get(path).status_code == 404
    settings.PROJECT_PUBLIC_PREVIEW_ENABLED = True
    document.scan_status = "pending"
    document.save()
    assert guest.get(path).status_code == 404


@pytest.fixture
def client(user):
    api_client = APIClient()
    api_client.force_authenticate(user)
    return api_client


@pytest.mark.django_db
def test_profile_is_owner_scoped(client, user):
    response = client.post("/api/v1/requirement-profiles/", {}, format="json")
    assert response.status_code == 201
    profile_id = response.json()["id"]

    other = User.objects.create_user(username="other", password="safe-test-password")
    other_client = APIClient()
    other_client.force_authenticate(other)
    assert other_client.get(f"/api/v1/requirement-profiles/{profile_id}/").status_code == 404


@pytest.mark.django_db
def test_profile_update_requires_etag(client):
    created = client.post("/api/v1/requirement-profiles/", {}, format="json")
    profile_id = created.json()["id"]
    response = client.patch(f"/api/v1/requirement-profiles/{profile_id}/", {"budget": {"currency": "INR", "purchaseMaximum": 10000000}}, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_submission_requires_saved_profile_consent(client, user):
    created = client.post("/api/v1/requirement-profiles/", {}, format="json")
    profile = RequirementProfile.objects.get(id=created.json()["id"])
    response = client.post(
        f"/api/v1/requirement-profiles/{profile.id}:submit",
        {},
        format="json",
        HTTP_IF_MATCH='W/"1"',
        HTTP_IDEMPOTENCY_KEY="submit-once",
    )
    assert response.status_code == 400

    Consent.objects.create(user=user, purpose="saved_profile", policy_version="1.0", granted_at=timezone.now())
    assert Consent.objects.filter(user=user, withdrawn_at__isnull=True).exists()


@pytest.mark.django_db
def test_buyer_login_requires_csrf(user):
    browser = APIClient(enforce_csrf_checks=True)
    credentials = {"username": "buyer", "password": "safe-test-password"}
    assert browser.post("/api/v1/auth/login", credentials, format="json").status_code == 403
    bootstrap = browser.get("/api/v1/auth/login")
    assert bootstrap.status_code == 200
    assert bootstrap["Cache-Control"] == "no-store"
    token = browser.cookies["csrftoken"].value
    assert browser.post("/api/v1/auth/login", credentials, format="json", HTTP_X_CSRFTOKEN=token).status_code == 200
    assert browser.get("/api/v1/auth/me").status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("anonymous", [False, True])
def test_buyer_catalogue_map_only_exposes_published_projects(client, anonymous):
    from platform_api.models import Organization, Project

    if anonymous:
        client = APIClient()
    organization = Organization.objects.create(legal_name="Map test organization")
    public = Project.objects.create(organization=organization, submitted_name="Published map test", maha_rera_id="P123456781", status="published", latitude="19.200000", longitude="72.950000")
    private = Project.objects.create(organization=organization, submitted_name="Private map test", maha_rera_id="P123456782", status="draft", latitude="19.210000", longitude="72.960000")
    response = client.get("/api/v1/projects/")
    assert response.status_code == 200
    records = response.json()["results"]
    assert [record["id"] for record in records] == [str(public.id)]
    assert float(records[0]["latitude"]) == 19.2
    assert float(records[0]["longitude"]) == 72.95
    assert client.get(f"/api/v1/projects/{private.id}/").status_code == 404
    detail = client.get(f"/api/v1/projects/{public.id}/")
    assert detail.status_code == 200
    if anonymous:
        assert set(detail.json()) == set(records[0])
        assert client.post("/api/v1/projects/", {}, format="json").status_code == 405
        for path in ("requirement-profiles/", "shortlists/", "consents/"):
            assert client.get(f"/api/v1/{path}").status_code == 403


@pytest.mark.django_db
def test_public_filters_ignore_legacy_configurations():
    from platform_api.models import Building, HomeConfiguration, Organization, Project

    project = Project.objects.create(organization=Organization.objects.create(legal_name="Public filter fixture"), submitted_name="Published", status="published", published_fact_set_version=1)
    building = Building.objects.create(project=project, name="A")
    HomeConfiguration.objects.create(building=building, configuration_type="2BHK", carpet_area_min=700, carpet_area_max=800, price_min=15000000, bathrooms=2, fact_set_version=1)
    HomeConfiguration.objects.create(building=building, configuration_type="4BHK", carpet_area_min=1400, carpet_area_max=1600, price_min=10000000, bathrooms=3, fact_set_version=2)
    client = APIClient()
    for query in ("configuration=4BHK", "carpetAreaMinimum=1500", "allInPriceMaximum=11000000"):
        assert client.get(f"/api/v1/projects/?{query}").json()["results"] == []
    assert client.get("/api/v1/projects/?configuration=2BHK").json()["results"] == []


@pytest.mark.django_db
@pytest.mark.parametrize("anonymous", [False, True])
def test_processing_catalogue_is_opt_in_and_withholds_draft_evidence(client, anonymous):
    from platform_api.models import Amenity, Building, HomeConfiguration, Organization, Project, ProjectAmenity, ReviewTask

    if anonymous:
        client = APIClient()
    organization = Organization.objects.create(legal_name="Processing fixture")
    projects = {state: Project.objects.create(organization=organization, submitted_name=state, locality="Thane", status=state)
                for state in ("published", "ingestion", "needs_review", "draft", "retired", "merged", "archived", "unexpected")}
    processing = projects["ingestion"]
    processing.latitude, processing.longitude = 19.15, 72.95
    processing.verification_status = "verified"
    processing.address = {"internalNote": "private evidence"}
    processing.save()
    building = Building.objects.create(project=processing, name="Private tower")
    HomeConfiguration.objects.create(building=building, configuration_type="4BHK", carpet_area_min=1400, carpet_area_max=1600, price_min=10000000, fact_set_version=0)
    amenity = Amenity.objects.create(taxonomy_code="draft-pool", display_name="Private pool", category="society")
    ProjectAmenity.objects.create(project=processing, amenity=amenity)
    for suffix in ("", "?includeProcessing=false"):
        assert [item["id"] for item in client.get("/api/v1/projects/" + suffix).json()["results"]] == [str(projects["published"].pk)]
    assert client.get("/api/v1/projects/?includeProcessing=1").status_code == 400
    response = client.get("/api/v1/projects/?includeProcessing=true")
    assert response.status_code == 200
    rows = {item["id"]: item for item in response.json()["results"]}
    assert set(rows) == {str(projects[state].pk) for state in ("published", "ingestion", "needs_review")}
    summary = rows[str(processing.pk)]
    assert summary["configurations"] == [] and summary["amenities"] == []
    assert summary["latitude"] is None and summary["longitude"] is None
    assert summary["verificationStatus"] == "unavailable" and summary["mahaReraId"] == ""
    assert set(summary) == set(rows[str(projects["published"].pk)])
    assert "private evidence" not in response.content.decode() and "Private tower" not in response.content.decode()
    for query in ("configuration=4BHK", "carpetAreaMinimum=1500", "allInPriceMaximum=11000000", "amenity=draft-pool", "mahaReraStatus=verified"):
        assert client.get("/api/v1/projects/?includeProcessing=true&" + query).json()["results"] == []
    assert client.get("/api/v1/projects/?includeProcessing=true&locality=Elsewhere").json()["results"] == []
    assert client.get(f"/api/v1/projects/{processing.pk}/?includeProcessing=true").status_code == 404
    ReviewTask.objects.create(project=processing, task_type="external_sources", state="resolved", resolution={
        "manualLocation": {"location": {"latitude": 19.15, "longitude": 72.95}}, "reviewState": "accepted"})
    rows = client.get("/api/v1/projects/?includeProcessing=true").json()["results"]
    assert float(next(item for item in rows if item["id"] == str(processing.pk))["latitude"]) == 19.15
    ReviewTask.objects.create(project=processing, task_type="external_sources", resolution={"googlePlaceId": "new-unreviewed", "reviewState": "unreviewed"})
    rows = client.get("/api/v1/projects/?includeProcessing=true").json()["results"]
    assert next(item for item in rows if item["id"] == str(processing.pk))["latitude"] is None


@pytest.mark.django_db
def test_public_station_map_deduplicates_and_preserves_evidence_status(settings, tmp_path, monkeypatch):
    from platform_api import map_catalogue
    from platform_api.models import Organization, Project, ReviewTask

    candidates = tmp_path / "stations.csv"
    candidates.write_text("name,line,latitude,longitude\nMetro one,Line 1,19.1,72.9\nMetro one,Line 1,19.1,72.9\nConflicting,Line 2,19.2,73\nConflicting,Line 2,19.3,73\nInvalid,Line 3,nan,73\n", encoding="utf-8")
    settings.PROJECT_MAP_STATION_CANDIDATES_FILE = str(candidates)
    settings.PROJECT_METRO_CATALOGUE = []
    organization = Organization.objects.create(legal_name="Map source fixture")
    railway = {"name": "Railway one", "kind": "railway", "location": {"latitude": 19.2, "longitude": 73},
               "serviceStatus": "operational", "walking": {"minutes": 12}, "sourceUrl": "javascript:bad"}
    for project_status, review_state, name in (("published", "accepted", "Railway one"), ("draft", "accepted", "Private"), ("published", "unreviewed", "Unreviewed")):
        project = Project.objects.create(organization=organization, submitted_name=name, status=project_status)
        ReviewTask.objects.create(project=project, task_type="external_sources", state="resolved" if review_state == "accepted" else "open",
                                  resolution={"reviewState": review_state, "submittedBy": "private-author", "stationRoutes": {"groups": [{"stations": [railway | {"name": name}]}]}})
    response = APIClient().get("/api/v1/map-stations")
    assert response.status_code == 200
    body = response.json()
    assert [item["name"] for item in body["stations"]] == ["Metro one", "Railway one"]
    assert all(item["serviceStatus"] == "unverified" for item in body["stations"])
    assert body["warnings"] == ["conflicting_candidates", "invalid_candidate"]
    assert "private-author" not in response.content.decode() and "javascript" not in response.content.decode()
    assert all("walking" not in item for item in body["stations"])
    monkeypatch.setattr(map_catalogue, "metro_catalogue", lambda: [{"name": "Metro one", "kind": "metro", "line": "Line 1", "location": {"latitude": 19.1, "longitude": 72.9}, "serviceStatus": "under_construction", "asOf": "2026-09-01", "sourceUrl": "https://mmrda.maharashtra.gov.in/"}])
    stations = APIClient().get("/api/v1/map-stations").json()["stations"]
    assert len(stations) == 2 and stations[0]["source"] == "authority"
    assert stations[0]["serviceStatus"] == "under_construction"
    settings.PROJECT_MAP_STATION_CANDIDATES_FILE = str(tmp_path / "missing.csv")
    assert "candidates_unavailable" in APIClient().get("/api/v1/map-stations").json()["warnings"]


@pytest.mark.django_db
def test_public_map_lines_follow_source_order_without_bridging_gaps(settings, tmp_path):
    candidates = tmp_path / "lines.csv"
    candidates.write_text(
        "name,line,latitude,longitude,kind\n"
        "Zulu,Line 1,19.1,72.9,metro\n"
        "Alpha,Line 1,19.2,72.9,metro\n"
        "Alpha,Line 1,19.2,72.9,metro\n"
        "Conflict,Line 1,19.3,72.9,metro\n"
        "Bravo,Line 1,19.4,72.9,metro\n"
        "Charlie,Line 1,19.5,72.9,metro\n"
        "Invalid,Line 1,nan,72.9,metro\n"
        "Delta,Line 1,19.6,72.9,metro\n"
        "Echo,Line 1,19.7,72.9,metro\n"
        "Shared,Line 2,19.7,72.9,metro\n"
        "Foxtrot,Line 2,19.8,72.9,metro\n"
        "Rail,Line 2,19.8,72.9,railway\n"
        "Rail end,Line 2,19.9,72.9,railway\n"
        "Conflict,Line 1,19.35,72.9,metro\n"
        "Unknown,,19.9,72.9,metro\n",
        encoding="utf-8",
    )
    settings.PROJECT_MAP_STATION_CANDIDATES_FILE = str(candidates)
    settings.PROJECT_METRO_CATALOGUE = []
    body = APIClient().get("/api/v1/map-stations").json()
    by_id = {station["id"]: station for station in body["stations"]}
    assert [[by_id[station_id]["name"] for station_id in line["stationIds"]] for line in body["lines"]] == [
        ["Zulu", "Alpha"], ["Bravo", "Charlie"], ["Delta", "Echo"], ["Shared", "Foxtrot"], ["Rail", "Rail end"],
    ]
    for line in body["lines"]:
        assert line["geometryStatus"] == "schematic_unverified"
        assert line["source"] == "user_candidate"
        assert all(by_id[station_id]["line"] == line["name"] and by_id[station_id]["kind"] == line["kind"] for station_id in line["stationIds"])
    assert body == APIClient().get("/api/v1/map-stations").json()
    settings.PROJECT_MAP_STATION_CANDIDATES_FILE = ""
    assert APIClient().get("/api/v1/map-stations").json()["lines"] == []


@pytest.mark.django_db
def test_buyer_snapshot_hides_withdrawn_projects(client, user):
    from platform_api.models import Organization, Project, RecommendationItem, RecommendationSnapshot, RequirementVersion

    profile = RequirementProfile.objects.create(owner=user)
    version = RequirementVersion.objects.create(profile=profile, version=1, payload={})
    snapshot = RecommendationSnapshot.objects.create(requirement_version=version, algorithm_version="match-1.0", input_catalog_version="test", state="ready")
    organization = Organization.objects.create(legal_name="Snapshot test organization")
    project = Project.objects.create(organization=organization, submitted_name="Withdrawn", maha_rera_id="P123456783", status="archived")
    RecommendationItem.objects.create(snapshot=snapshot, project=project, rank=1, score=80, confidence=.8, components=[], reason_codes=[])
    response = client.get(f"/api/v1/recommendation-requests/{snapshot.id}/")
    assert response.status_code == 200
    assert response.json()["items"] == []


@pytest.mark.django_db
def test_buyer_amenities_and_collections_use_wire_contract(client):
    from platform_api.models import Amenity

    Amenity.objects.create(taxonomy_code="gym", display_name="Gym", category="society")
    assert client.get("/api/v1/amenities").json() == [{"taxonomyCode": "gym", "displayName": "Gym", "category": "society"}]
    assert APIClient().get("/api/v1/amenities").status_code == 403
    assert client.get("/api/v1/shortlists/").status_code == 200
    assert client.get("/api/v1/consents/").status_code == 200


@pytest.mark.django_db
def test_buyer_submission_to_score_sheet(client, user, layout):
    from platform_api.models import RecommendationSnapshot
    from platform_api.services import compute_recommendations

    plan, _ = layout
    project = plan.project
    plan.configuration_type, plan.carpet_area_sqft, plan.bathroom_count = "2BHK", 850, 2
    plan.save()
    payload = {"places": [], "home": {"configurations": ["2BHK"], "carpetAreaSqFt": {"minimum": 650, "preferred": 800}, "bathroomsMinimum": 2, "bathroomsIsHardConstraint": True}, "budget": {"currency": "INR", "purchaseMaximum": 20000000, "allInMaximum": 22000000, "isHardConstraint": True}, "societyAmenities": [], "nearbyAmenities": [], "microMarket": {"preferredLocalities": []}, "weights": {"commute": .30, "budget": .25, "space": .20, "societyAmenities": .10, "nearbyAmenities": .05, "microMarket": .05, "projectConfidence": .05}, "additionalNeeds": ""}
    Consent.objects.create(user=user, purpose="saved_profile", policy_version="1.0", granted_at=timezone.now())
    created = client.post("/api/v1/requirement-profiles/", {}, format="json").json()
    saved = client.patch(f"/api/v1/requirement-profiles/{created['id']}/", payload, format="json", HTTP_IF_MATCH='W/"1"')
    assert saved.status_code == 200
    result = client.post(f"/api/v1/requirement-profiles/{created['id']}:submit", {}, format="json", HTTP_IF_MATCH='W/"2"', HTTP_IDEMPOTENCY_KEY="buyer-flow")
    assert result.status_code == 202
    snapshot = RecommendationSnapshot.objects.get(id=result.json()["recommendationRequestId"])
    compute_recommendations(snapshot.requirement_version, snapshot=snapshot)
    score_sheet = client.get(f"/api/v1/recommendation-requests/{snapshot.id}/").json()
    assert score_sheet["status"] == "ready"
    assert score_sheet["profileVersion"] == 2
    assert score_sheet["items"][0]["projectId"] == str(project.id)
    assert score_sheet["items"][0]["components"][0]["status"] == "unavailable"
    assert "submittedName" in client.get(f"/api/v1/projects/{project.id}/").json()


@pytest.mark.django_db
@pytest.mark.parametrize("payload, field", [
    ({"home": {"carpetAreaSqFt": {"minimum": "large", "maximum": 900}}}, "home.carpetAreaSqFt"),
    ({"budget": {"purchaseMaximum": 0}}, "budget"),
    ({"weights": []}, "weights"),
])
def test_signed_in_requirement_validation_returns_readable_400(client, payload, field):
    created = client.post("/api/v1/requirement-profiles/", {}, format="json").json()
    response = client.patch(f"/api/v1/requirement-profiles/{created['id']}/", payload, format="json", HTTP_IF_MATCH='W/"1"')
    assert response.status_code == 400
    assert response.json()["errors"][field][0]["message"]
    profile = RequirementProfile.objects.get(pk=created["id"])
    assert profile.active_version.version == 1
    assert profile.versions.count() == 1
