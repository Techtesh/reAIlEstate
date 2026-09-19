import hashlib
import io
from datetime import timedelta

import pymupdf
import pytest
from django.contrib.auth.models import User
from django.utils import timezone
from platform_api.artifact_storage import storage as default_storage
from PIL import Image

from platform_api.floor_plan_agent import FloorPlanAgent, FloorPlanVisionProvider
from platform_api.floor_plan_pages import FloorPlanPageSelector
from platform_api.models import EvidenceDocument, Organization, Project
from platform_api.rag import ingest_document


@pytest.fixture
def source(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.FLOOR_PLAN_RENDER_DPI = 240
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Lifestyle amenities and artist impression")
        page = pdf.new_page()
        page.insert_text((72, 72), "Unit plan 2 BHK RERA carpet area: 720 sqft Living 12 x 18 ft")
        content = pdf.tobytes()
    user = User.objects.create_user(username="pdf-builder")
    project = Project.objects.create(
        organization=Organization.objects.create(legal_name="PDF Builder"), submitted_name="PDF Project",
    )
    document = EvidenceDocument.objects.create(
        project=project, object_key="quarantine/test.pdf", original_name="plans.pdf",
        declared_media_type="application/pdf", byte_length=len(content),
        sha256=hashlib.sha256(content).hexdigest(), uploaded_by=user,
        retention_until=timezone.now() + timedelta(days=7), scan_status="clean", status="extracting",
    )
    return ingest_document(document, content), content


class RecordingVision:
    code = "ollama"
    model = "test-vision"
    profile_id = "local-test"

    def __init__(self):
        self.inputs = []

    def analyze(self, image_bytes, media_type, fixed_schema):
        assert media_type == "image/png"
        assert fixed_schema["additionalProperties"] is False
        self.inputs.append(image_bytes)
        return {"floorPlans": [{
            "planName": "Unit A", "planScope": "flat", "configurationType": "2BHK",
            "carpetAreaSqFt": 720, "orientation": "North", "features": ["balcony"],
            "dimensions": [{"room": "living", "lengthFt": 12, "widthFt": 18}], "confidence": 0.9,
        }]}


@pytest.mark.django_db
def test_pdf_renders_selected_page_before_vision_and_records_provenance(source):
    run, content = source
    provider = RecordingVision()
    workflow = FloorPlanAgent(provider).run(run, content, "application/pdf")

    assert workflow.status == "completed"
    assert len(provider.inputs) == 1
    asset = workflow.floor_plan_assets.get()
    assert asset.page_number == 2
    assert asset.asset_type == "page_render"
    assert asset.source_bounds["renderDpi"] == 240
    assert asset.pixel_width >= 1900 and asset.pixel_height >= 2700
    with default_storage.open(asset.object_key, "rb") as stored:
        assert stored.read() == provider.inputs[0]
    assert hashlib.sha256(provider.inputs[0]).hexdigest() == asset.sha256
    plan = workflow.floor_plans.get()  # No duplicate regex candidate for the same rendered page.
    assert plan.plan_name.startswith("Unit A [AI ")
    assert plan.orientation == "North"
    assert plan.features == {"items": ["balcony"]}
    assert plan.raw_extraction["visionProfileId"] == "local-test"
    assert plan.raw_extraction["visionModel"] == "test-vision"
    assert plan.review_state == "gen_with_ai_needs_review"
    selection = workflow.steps.get(name="select_pages").output
    assert [item["pageNumber"] for item in selection["pages"]] == [2]
    assert selection["pages"][0]["reasons"]


@pytest.mark.django_db
def test_text_rich_pdf_still_requires_vision_when_provider_unavailable(source):
    run, content = source
    workflow = FloorPlanAgent(FloorPlanVisionProvider()).run(run, content, "application/pdf")
    assert workflow.status == "needs_provider"
    assert not workflow.floor_plans.exists()
    assert workflow.floor_plan_assets.count() == 1


def test_scanned_pdf_selection_is_bounded():
    image = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(image, format="PNG")
    with pymupdf.open() as pdf:
        for _ in range(12):
            page = pdf.new_page()
            page.insert_image(page.rect, stream=image.getvalue())
        content = pdf.tobytes()
    selected = FloorPlanPageSelector().select(content, "application/pdf")
    assert len(selected) == 8
    assert all("image_with_little_text" in page.reasons for page in selected)


def test_vision_keeps_labeled_narrow_spaces():
    result = {"floorPlans": [{
        "planName": "Unit 6", "planScope": "flat", "configurationType": "1BHK",
        "carpetAreaSqFt": None, "orientation": None, "features": [], "confidence": 0.9,
        "dimensions": [
            {"room": "Wash Basin Nook", "lengthFt": 1.5, "widthFt": 3.92},
            {"room": "Bedroom Foyer", "lengthFt": 3.08, "widthFt": 4.42},
        ],
    }]}
    agent = FloorPlanAgent(RecordingVision())
    candidates = agent._validate(agent._vision_candidates(1, result))
    assert candidates[0].dimensions == result["floorPlans"][0]["dimensions"]