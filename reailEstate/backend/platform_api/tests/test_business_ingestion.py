import hashlib
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from platform_api.artifact_storage import storage as default_storage
from django.utils import timezone

from platform_api.models import EvidenceDocument, FactCandidate, FloorPlan, FloorPlanAsset, Organization, Project, ReviewTask
from platform_api.workers import process_document
from platform_api.rag import SourcePage, chunk_pages, sanitize_extracted_text


class CleanTestScanner:
    def scan(self, file_object):
        file_object.seek(0)
        return "clean"


def test_extracted_text_removes_postgres_nul_bytes():
    assert sanitize_extracted_text("Highland\x00 Haven") == "Highland Haven"
    assert chunk_pages([SourcePage(7, "Garden\x00 and pool", {})])[0].text == "Garden and pool"


@pytest.mark.django_db
def test_markdown_ingestion_extracts_facts_and_floor_plan(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    user = User.objects.create_user(username="builder")
    organization = Organization.objects.create(legal_name="Builder Ltd")
    project = Project.objects.create(
        organization=organization,
        maha_rera_id="P123456789",
        submitted_name="Harbour Heights",
        status="ingestion",
    )
    content = b"""# Project information
Project name: Harbour Heights
Location: Chembur, Mumbai
Promoter: Builder Ltd
MahaRERA: P123456789
Price: INR 2.5 crore
Possession: December 2028

## Floor Plan
2 BHK unit plan
RERA carpet area: 720 sqft
Living 12 x 18 ft
Bedroom 10 x 12 ft
Bathroom 5 x 8 ft
Amenities: swimming pool, gym, clubhouse and garden.
"""
    object_key = "quarantine/test/project.md"
    default_storage.save(object_key, ContentFile(content))
    document = EvidenceDocument.objects.create(
        project=project,
        object_key=object_key,
        original_name="project.md",
        declared_media_type="text/markdown",
        byte_length=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        status="quarantined",
        uploaded_by=user,
        retention_until=timezone.now() + timedelta(days=7),
    )

    processed = process_document(document.id, scanner=CleanTestScanner())

    assert processed.status == "needs_review"
    assert processed.ingestion_runs.filter(status="completed").exists()
    assert FactCandidate.objects.filter(project=project, field_code="price_inr").exists()
    plan = FloorPlan.objects.get(project=project)
    assert plan.configuration_type == "2BHK"
    assert float(plan.carpet_area_sqft) == 720
    assert len(plan.dimensions) == 3
    assert ReviewTask.objects.filter(project=project, task_type="floor_plan", state="open").exists()


@pytest.mark.django_db
def test_image_only_floor_plan_waits_for_vision_provider(tmp_path, settings):
    from io import BytesIO
    from PIL import Image

    settings.MEDIA_ROOT = tmp_path
    settings.FLOOR_PLAN_EXTRACTION_MODE = "ai"
    settings.FLOOR_PLAN_VISION_PROFILES = {}
    settings.FLOOR_PLAN_DEFAULT_VISION_PROFILE = ""
    settings.FLOOR_PLAN_VISION_PROVIDER = "unavailable"
    user = User.objects.create_user(username="image-builder")
    organization = Organization.objects.create(legal_name="Image Builder")
    project = Project.objects.create(organization=organization, maha_rera_id="P987654321", submitted_name="Image Project")
    image_buffer = BytesIO()
    Image.new("RGB", (100, 100), "white").save(image_buffer, format="PNG")
    content = image_buffer.getvalue()
    object_key = "quarantine/test/plan.png"
    default_storage.save(object_key, ContentFile(content))
    document = EvidenceDocument.objects.create(
        project=project,
        object_key=object_key,
        original_name="plan.png",
        declared_media_type="image/png",
        byte_length=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        status="quarantined",
        uploaded_by=user,
        retention_until=timezone.now() + timedelta(days=7),
    )

    processed = process_document(document.id, scanner=CleanTestScanner())

    assert processed.status == "needs_review"
    assert project.agent_workflow_runs.filter(status="needs_provider").exists()
    assert ReviewTask.objects.filter(project=project, task_type="floor_plan_vision").exists()
    asset = FloorPlanAsset.objects.get(project=project)
    assert asset.media_type == "image/png"
    assert asset.pixel_width > 0 and asset.pixel_height > 0
    assert asset.source_bounds["unit"] == "normalized"
    assert default_storage.exists(asset.object_key)
