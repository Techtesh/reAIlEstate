"""Verified original-document reads, counts and standalone page rendering.

These implementations retain the source-page facade's scan/type checks
and error messages. They do not authorize callers
or persist anything. Rendering still depends on FloorPlanImageExtractor's private
_render_pdf_crop and _bound_image methods to preserve current pixels and bounds.
"""

import hashlib
import io

import pymupdf
from PIL import Image, ImageOps

from ..artifact_storage import storage
from ..floor_plan_extractor import FloorPlanImageExtractor


SOURCE_TYPES = ("application/pdf", "image/png", "image/jpeg")


def read_source(document):
    if document.scan_status != "clean":
        raise ValueError("Source must complete malware scanning before page selection.")
    if document.declared_media_type not in SOURCE_TYPES:
        raise ValueError("Choose a PDF, PNG or JPEG source document.")
    try:
        with storage.open(document.object_key, "rb") as stream:
            data = stream.read(document.byte_length + 1)
    except FileNotFoundError:
        raise ValueError("Original source is missing; restore the source document first.") from None
    if len(data) != document.byte_length or hashlib.sha256(data).hexdigest() != document.sha256:
        raise ValueError("Source checksum validation failed.")
    return data


def _open_pdf(data):
    try:
        return pymupdf.open(stream=data, filetype="pdf")
    except (pymupdf.FileDataError, pymupdf.EmptyFileError):
        raise ValueError("Source PDF is unreadable. Choose another document or repair its ingestion.") from None


def source_page_count(document):
    data = read_source(document)
    if document.declared_media_type == "application/pdf":
        with _open_pdf(data) as pdf:
            if pdf.needs_pass:
                raise ValueError("Password-protected PDF cannot be used.")
            return pdf.page_count
    with Image.open(io.BytesIO(data)) as image:
        image.verify()
    return 1


def render_source_pages(document, pages):
    data = read_source(document)
    renderer = FloorPlanImageExtractor()
    if document.declared_media_type == "application/pdf":
        with _open_pdf(data) as pdf:
            if pdf.needs_pass or any(page < 1 or page > pdf.page_count for page in pages):
                raise ValueError("Select valid pages from this PDF.")
            return [(page, renderer._render_pdf_crop(pdf[page - 1], page, pdf[page - 1].rect, 0).content)
                    for page in pages]
    if pages != [1]:
        raise ValueError("An image source has only page 1.")
    with Image.open(io.BytesIO(data)) as original:
        image = renderer._bound_image(ImageOps.exif_transpose(original).convert("RGB"))
        try:
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return [(1, buffer.getvalue())]
        finally:
            image.close()