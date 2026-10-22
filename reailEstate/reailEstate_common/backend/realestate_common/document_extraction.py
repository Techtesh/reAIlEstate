import io
import zipfile
from dataclasses import dataclass

import pymupdf
from PIL import Image, ImageChops, UnidentifiedImageError


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


def open_image(content):
    Image.MAX_IMAGE_PIXELS = MAX_CROP_PIXELS
    image = Image.open(io.BytesIO(content))
    image.load()
    if image.width * image.height > MAX_CROP_PIXELS:
        image.close()
        raise ValueError("FLOOR_PLAN_IMAGE_TOO_LARGE")
    return image


def bound_image(image):
    if image.width * image.height <= MAX_CROP_PIXELS:
        return image
    ratio = (MAX_CROP_PIXELS / (image.width * image.height)) ** 0.5
    return image.resize(
        (max(1, round(image.width * ratio)), max(1, round(image.height * ratio))),
        Image.Resampling.LANCZOS,
    )


def render_pdf_crop(page, page_number, rect, confidence, render_dpi=PDF_RENDER_DPI):
    render_dpi = max(144, min(int(render_dpi), 360))
    requested_scale = render_dpi / 72
    pixel_safe_scale = (MAX_CROP_PIXELS * 0.98 / max(1, rect.width * rect.height)) ** 0.5
    scale = min(requested_scale, pixel_safe_scale)
    pixmap = page.get_pixmap(
        matrix=pymupdf.Matrix(scale, scale),
        clip=rect,
        alpha=False,
        colorspace=pymupdf.csRGB,
    )
    image = Image.open(io.BytesIO(pixmap.tobytes("png")))
    try:
        image = bound_image(image)
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


def extract_source_image(content, page_number, confidence):
    image = open_image(content)
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
                max(0, left - padding),
                max(0, top - padding),
                min(original_width, right + padding),
                min(original_height, bottom + padding),
            )
            if (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) >= MIN_CROP_PIXELS:
                rgb = rgb.crop(bbox)
            else:
                bbox = (0, 0, original_width, original_height)
        else:
            bbox = (0, 0, original_width, original_height)
        rgb = bound_image(rgb)
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


def extract_docx_images(content, plan_context_detected, max_images):
    if not plan_context_detected:
        return []
    candidates = []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        media_names = [name for name in archive.namelist() if name.casefold().startswith("word/media/")]
        for name in media_names[:max_images]:
            raw = archive.read(name)
            try:
                candidate = extract_source_image(raw, page_number=None, confidence=0.65)
            except (UnidentifiedImageError, ValueError, Image.DecompressionBombError):
                continue
            candidates.append(
                CropCandidate(
                    page_number=None,
                    content=candidate.content,
                    source_bounds=candidate.source_bounds | {"docxPart": name},
                    confidence=candidate.confidence,
                )
            )
    return candidates
