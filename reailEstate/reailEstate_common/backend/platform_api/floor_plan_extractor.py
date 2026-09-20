import hashlib
import uuid

import pymupdf
from django.conf import settings
from django.core.files.base import ContentFile
from realestate_common.document_extraction import (
    CropCandidate,
    MAX_CROP_PIXELS,
    MIN_CROP_PIXELS,
    PDF_RENDER_DPI,
    bound_image,
    extract_docx_images,
    extract_source_image,
    open_image,
    render_pdf_crop,
)
from .artifact_storage import storage as default_storage

from .models import FloorPlanAsset
from .floor_plan_rules import classify_plan_scope


class FloorPlanImageExtractor:
    """Saves selected PDF pages at high resolution, or cropped standalone images."""

    version = "floor-plan-cropper-1.0"

    def extract_and_store(self, workflow, content, media_type, contexts):
        page_scores = self._page_scores(contexts)
        if media_type == "application/pdf":
            candidates = self._from_pdf(content, page_scores)
        elif media_type in {"image/png", "image/jpeg"}:
            candidates = [self._from_image(content, page_number=1, confidence=1.0)]
        elif media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            candidates = self._from_docx(content, bool(contexts))
        else:
            candidates = []
        assets = []
        for candidate in candidates[:settings.FLOOR_PLAN_MAX_PAGES]:
            digest = hashlib.sha256(candidate.content).hexdigest()
            image = self._open_image(candidate.content)
            width, height = image.size
            image.close()
            text = " ".join(item.get("text", item["chunk"].content) for item in contexts
                            if item["chunk"].page_number == candidate.page_number)
            scope = classify_plan_scope(text)
            explicit_layout = any(
                item.get("explicitLayout") and item["chunk"].page_number == candidate.page_number
                for item in contexts
            )
            category = "layouts" if explicit_layout else {"floor": "floor-plans", "flat": "layouts"}.get(scope, "unclassified")
            object_key = f"output/media/images/{workflow.project_id}/{category}/{workflow.id}/{uuid.uuid4().hex}.png"
            stored_key = default_storage.save(object_key, ContentFile(candidate.content))
            try:
                asset, created = FloorPlanAsset.objects.get_or_create(
                    workflow_run=workflow,
                    sha256=digest,
                    defaults={
                        "project": workflow.project,
                        "document": workflow.document,
                        "page_number": candidate.page_number,
                        "asset_type": candidate.asset_type,
                        "object_key": stored_key,
                        "byte_length": len(candidate.content),
                        "pixel_width": width,
                        "pixel_height": height,
                        "source_bounds": candidate.source_bounds | {"category": category},
                        "detector_version": self.version,
                        "detection_confidence": candidate.confidence,
                    },
                )
                if not created:
                    default_storage.delete(stored_key)
                assets.append(asset)
            except Exception:
                default_storage.delete(stored_key)
                raise
        return assets

    def _from_pdf(self, content, page_scores):
        pymupdf.TOOLS.mupdf_display_errors(False)
        pymupdf.TOOLS.mupdf_display_warnings(False)
        document = None
        candidates = []
        try:
            document = pymupdf.open(stream=content, filetype="pdf")
            ranked_pages = sorted(page_scores.items(), key=lambda item: (-item[1], item[0]))
            for page_number, score in ranked_pages:
                if not 1 <= page_number <= document.page_count:
                    continue
                from .ingestion_runtime import event
                event("render_page", pageNumber=page_number, renderedPages=len(candidates), selectedPages=len(ranked_pages))
                page = document[page_number - 1]
                page_rect = page.rect
                candidates.append(self._render_pdf_crop(page, page_number, page_rect, score))
                if len(candidates) >= settings.FLOOR_PLAN_MAX_PAGES:
                    break
        finally:
            if document is not None:
                document.close()
            pymupdf.TOOLS.mupdf_display_errors(True)
            pymupdf.TOOLS.mupdf_display_warnings(True)
        return candidates

    def _render_pdf_crop(self, page, page_number, rect, confidence):
        return render_pdf_crop(
            page,
            page_number,
            rect,
            confidence,
            getattr(settings, "FLOOR_PLAN_RENDER_DPI", PDF_RENDER_DPI),
        )

    def _from_image(self, content, page_number, confidence):
        return extract_source_image(content, page_number, confidence)

    def _from_docx(self, content, plan_context_detected):
        return extract_docx_images(content, plan_context_detected, settings.FLOOR_PLAN_MAX_PAGES)

    @staticmethod
    def _page_scores(contexts):
        scores = {}
        for item in contexts:
            page_number = item["chunk"].page_number
            if page_number is not None:
                scores[page_number] = max(scores.get(page_number, 0), float(item.get("score", 0.5)))
        return scores

    @staticmethod
    def _expand_rect(rect, bounds, padding):
        return pymupdf.Rect(
            max(bounds.x0, rect.x0 - padding),
            max(bounds.y0, rect.y0 - padding),
            min(bounds.x1, rect.x1 + padding),
            min(bounds.y1, rect.y1 + padding),
        )

    @staticmethod
    def _open_image(content):
        return open_image(content)

    @staticmethod
    def _bound_image(image):
        return bound_image(image)
