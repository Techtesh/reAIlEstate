"""Candidate page classification and private URI manifests, separate from model analysis."""

import hashlib
import re
from uuid import uuid4

import pymupdf
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from PIL import Image
import io

from .artifact_storage import storage
from .amenity_matching import has_amenity_terms
from .floor_plan_extractor import FloorPlanImageExtractor
from .models import FloorPlanAsset
from .document_formats import DOCX


CATEGORY_PATTERNS = {
    "amenities": re.compile(
        r"\b(?:amenities|club\s*house|swimming\s*pool|gym(?:nasium)?|landscap(?:e|ed)\s*garden|"
        r"jogging\s*track|senior\s+citizen|amphitheat(?:re|er)|multi[ -]?purpose\s+(?:court|lawn|room)|"
        r"kids?\s*play\s*area|yoga\s*deck|(?:baby\s+)?cr[eè]che|library|reading\s+area|steam\s+room|"
        r"guest\s+room|indoor\s+games?)\b", re.I,
    ),
    "general": re.compile(r"\b(?:elevation|facade|façade|architecture|artist(?:'s)?\s*impression|exterior|interior|design)\b", re.I),
}


def collect_document_images(workflow, content, media_type):
    existing = list(workflow.floor_plan_assets.all())
    for asset in existing:
        scopes = set(asset.floor_plans.filter(workflow_run=workflow).values_list("plan_scope", flat=True))
        category = "layouts" if scopes == {"flat"} else "floor-plans" if scopes == {"floor"} else "unclassified"
        if asset.source_bounds.get("category") != category:
            old_key = asset.object_key
            with storage.open(old_key) as image:
                saved = storage.save(f"output/media/images/{workflow.project_id}/{category}/{workflow.id}/{uuid4().hex}.png", ContentFile(image.read()))
            asset.object_key = saved
            asset.source_bounds = asset.source_bounds | {"category": category}
            asset.save(update_fields=["object_key", "source_bounds", "updated_at"])
            transaction.on_commit(lambda key=old_key: storage.delete(key), robust=True)
    represented = {asset.page_number for asset in existing}
    if media_type == "application/pdf":
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            count = 0
            for index, page in enumerate(pdf, start=1):
                if index in represented:
                    continue
                text = page.get_text("text")[:100_000]
                category = "amenities" if has_amenity_terms(text) else next(
                    (name for name, pattern in CATEGORY_PATTERNS.items() if pattern.search(text)), None
                )
                if category is None or not (page.get_images() or page.get_drawings()):
                    continue
                if count >= settings.DOCUMENT_EXTRA_IMAGE_LIMIT:
                    break
                crop = FloorPlanImageExtractor()._render_pdf_crop(page, index, page.rect, 0.5)
                digest = hashlib.sha256(crop.content).hexdigest()
                if workflow.floor_plan_assets.filter(sha256=digest).exists():
                    continue
                key = f"output/media/images/{workflow.project_id}/{category}/{workflow.id}/{uuid4().hex}.png"
                stored = storage.save(key, ContentFile(crop.content))
                try:
                    with Image.open(io.BytesIO(crop.content)) as image:
                        width, height = image.size
                    asset = FloorPlanAsset.objects.create(
                        project=workflow.project, document=workflow.document, workflow_run=workflow,
                        page_number=index, asset_type="page_render", object_key=stored, byte_length=len(crop.content),
                        sha256=digest, pixel_width=width, pixel_height=height,
                        source_bounds=crop.source_bounds | {"category": category, "classification": "candidate", "reason": "page_text_and_graphics"},
                        detector_version="document-page-classifier-1.0", detection_confidence=0.5,
                    )
                    existing.append(asset)
                    count += 1
                except Exception:
                    storage.delete(stored)
                    raise
    if media_type == DOCX and not existing:
        text = " ".join(workflow.ingestion_run.chunks.values_list("content", flat=True))
        category = "amenities" if has_amenity_terms(text) else next(
            (name for name, pattern in CATEGORY_PATTERNS.items() if pattern.search(text)), "unclassified"
        )
        for crop in FloorPlanImageExtractor()._from_docx(content, True):
            digest = hashlib.sha256(crop.content).hexdigest()
            if workflow.floor_plan_assets.filter(sha256=digest).exists():
                continue
            key = f"output/media/images/{workflow.project_id}/{category}/{workflow.id}/{uuid4().hex}.png"
            saved = storage.save(key, ContentFile(crop.content))
            with Image.open(io.BytesIO(crop.content)) as image:
                width, height = image.size
            existing.append(FloorPlanAsset.objects.create(
                project=workflow.project, document=workflow.document, workflow_run=workflow,
                page_number=None, asset_type="embedded_image", object_key=saved, byte_length=len(crop.content),
                sha256=digest, pixel_width=width, pixel_height=height,
                source_bounds=crop.source_bounds | {"category": category, "reason": "document_level_context"},
                detector_version="document-page-classifier-1.0", detection_confidence=0.25,
            ))
    return [{"assetId": str(asset.id), "pageNumber": asset.page_number,
             "category": asset.source_bounds.get("category", "unclassified"),
             "classification": "candidate", "confidence": asset.detection_confidence,
             "uri": f"/api/v1/media/floor-plan-assets/{asset.id}", "sha256": asset.sha256,
             "detectorVersion": asset.detector_version} for asset in existing]