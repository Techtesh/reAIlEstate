import hashlib
import io
import uuid
import zipfile
from dataclasses import dataclass

import pymupdf
from django.conf import settings
from django.core.files.base import ContentFile
from .artifact_storage import storage as default_storage
from PIL import Image, ImageChops, UnidentifiedImageError

from .models import FloorPlanAsset
from .floor_plan_rules import classify_plan_scope


MAX_CROP_PIXELS = 40_000_000
MIN_CROP_PIXELS = 40_000
PDF_RENDER_DPI = 240


@dataclass(frozen=True)
class CropCandidate:
    page_number: int | None
    content: bytes
    source_bounds: dict
    confidence: float
    asset_type: str = "plan_crop"


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
        render_dpi = max(144, min(int(getattr(settings, "FLOOR_PLAN_RENDER_DPI", PDF_RENDER_DPI)), 360))
        requested_scale = render_dpi / 72
        pixel_safe_scale = (MAX_CROP_PIXELS * 0.98 / max(1, rect.width * rect.height)) ** 0.5
        scale = min(requested_scale, pixel_safe_scale)
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=rect, alpha=False, colorspace=pymupdf.csRGB)
        image = Image.open(io.BytesIO(pixmap.tobytes("png")))
        try:
            image = self._bound_image(image)
            output = io.BytesIO()
            image.save(output, format="PNG", optimize=True)
        finally:
            image.close()
        page_rect = page.rect
        return CropCandidate(
            page_number=page_number,
            content=output.getvalue(),
            source_bounds={
                "unit": "normalized",
                "x": round((rect.x0 - page_rect.x0) / page_rect.width, 6),
                "y": round((rect.y0 - page_rect.y0) / page_rect.height, 6),
                "width": round(rect.width / page_rect.width, 6),
                "height": round(rect.height / page_rect.height, 6),
                "renderDpi": round(scale * 72, 2),
            },
            confidence=max(0, min(1, confidence)),
            asset_type="page_render",
        )

    def _from_image(self, content, page_number, confidence):
        image = self._open_image(content)
        try:
            original_width, original_height = image.size
            rgb = image.convert("RGB")
            background = Image.new("RGB", rgb.size, "white")
            difference = ImageChops.difference(rgb, background).convert("L")
            bbox = difference.point(lambda value: 255 if value > 18 else 0).getbbox()
            if bbox:
                left, top, right, bottom = bbox
                padding = max(8, round(min(rgb.size) * 0.02))
                bbox = (
                    max(0, left - padding), max(0, top - padding),
                    min(original_width, right + padding), min(original_height, bottom + padding),
                )
                if (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) >= MIN_CROP_PIXELS:
                    rgb = rgb.crop(bbox)
                else:
                    bbox = (0, 0, original_width, original_height)
            else:
                bbox = (0, 0, original_width, original_height)
            rgb = self._bound_image(rgb)
            output = io.BytesIO()
            rgb.save(output, format="PNG", optimize=True)
            return CropCandidate(
                page_number=page_number,
                content=output.getvalue(),
                source_bounds={
                    "unit": "normalized",
                    "x": round(bbox[0] / original_width, 6),
                    "y": round(bbox[1] / original_height, 6),
                    "width": round((bbox[2] - bbox[0]) / original_width, 6),
                    "height": round((bbox[3] - bbox[1]) / original_height, 6),
                },
                confidence=confidence,
                asset_type="source_image",
            )
        finally:
            image.close()

    def _from_docx(self, content, plan_context_detected):
        if not plan_context_detected:
            return []
        candidates = []
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            media_names = [name for name in archive.namelist() if name.casefold().startswith("word/media/")]
            for name in media_names[:settings.FLOOR_PLAN_MAX_PAGES]:
                raw = archive.read(name)
                try:
                    candidate = self._from_image(raw, page_number=None, confidence=0.65)
                except (UnidentifiedImageError, ValueError, Image.DecompressionBombError):
                    continue
                candidates.append(CropCandidate(
                    page_number=None,
                    content=candidate.content,
                    source_bounds=candidate.source_bounds | {"docxPart": name},
                    confidence=candidate.confidence,
                ))
        return candidates

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
        Image.MAX_IMAGE_PIXELS = MAX_CROP_PIXELS
        image = Image.open(io.BytesIO(content))
        image.load()
        if image.width * image.height > MAX_CROP_PIXELS:
            image.close()
            raise ValueError("FLOOR_PLAN_IMAGE_TOO_LARGE")
        return image

    @staticmethod
    def _bound_image(image):
        if image.width * image.height <= MAX_CROP_PIXELS:
            return image
        ratio = (MAX_CROP_PIXELS / (image.width * image.height)) ** 0.5
        return image.resize((max(1, round(image.width * ratio)), max(1, round(image.height * ratio))), Image.Resampling.LANCZOS)
