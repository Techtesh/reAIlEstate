import hashlib
import io
from types import SimpleNamespace
from unittest.mock import Mock

import pymupdf
import pytest
from PIL import Image

from platform_api import layout_pages, source_page_additions
from platform_api.infrastructure import source_documents


@pytest.fixture
def source_pdf(monkeypatch):
    with pymupdf.open() as pdf:
        for _ in range(3):
            pdf.new_page(width=72, height=72)
        data = pdf.tobytes()
    chunks = Mock()
    chunks.aggregate.return_value = {"value": 1}
    document = SimpleNamespace(
        scan_status="clean", declared_media_type="application/pdf",
        object_key="test/source.pdf", original_name="source.pdf",
        byte_length=len(data), sha256=hashlib.sha256(data).hexdigest(),
        knowledge_chunks=chunks,
    )
    # Patching a method on the historical facade's storage still reaches the new module.
    opener = Mock(side_effect=lambda *args: io.BytesIO(data))
    monkeypatch.setattr(source_page_additions.storage, "open", opener)
    return document, data, opener


def test_old_and_new_imports_are_the_same_helpers_and_shared_dependencies():
    for name in (
        "read_source", "source_page_count", "render_source_pages", "SOURCE_TYPES",
        "storage", "settings", "Path", "pymupdf", "Image", "ImageOps", "FloorPlanImageExtractor",
    ):
        assert getattr(source_page_additions, name) is getattr(source_documents, name)


@pytest.mark.parametrize("api", [source_page_additions, source_documents])
def test_both_import_paths_read_count_and_render(source_pdf, api):
    document, data, opener = source_pdf
    assert api.read_source(document) == data
    assert api.source_page_count(document) == 3
    rendered = api.render_source_pages(document, [3, 1])
    assert [page for page, _ in rendered] == [3, 1]
    for _, png in rendered:
        with Image.open(io.BytesIO(png)) as image:
            assert image.format == "PNG"
            image.verify()
    assert opener.call_count == 3


def test_actual_page_count_exceeds_partial_knowledge_chunk_maximum(source_pdf):
    document, _, _ = source_pdf
    assert layout_pages.document_page_count(document) == 3
    assert layout_pages.validate_document_page(document, 3) == 3
    document.knowledge_chunks.aggregate.assert_not_called()
    for page in (0, 4):
        with pytest.raises(ValueError, match="^PAGE_NUMBER_OUT_OF_RANGE$"):
            layout_pages.validate_document_page(document, page)


def test_count_delegates_to_verified_source_helper(source_pdf, monkeypatch):
    document, _, _ = source_pdf
    counter = Mock(return_value=7)
    monkeypatch.setattr(layout_pages, "source_page_count", counter)
    assert layout_pages.document_page_count(document) == 7
    counter.assert_called_once_with(document)


def test_count_rejects_corruption_even_when_chunks_exist(source_pdf):
    document, data, opener = source_pdf
    opener.side_effect = lambda *args: io.BytesIO(b"!" + data[1:])
    with pytest.raises(ValueError, match="^Source checksum validation failed\\.$"):
        layout_pages.document_page_count(document)
    document.knowledge_chunks.aggregate.assert_not_called()


@pytest.mark.parametrize("reader", [source_documents.read_source, layout_pages.document_page_count])
def test_source_scan_gate_precedes_storage(source_pdf, reader):
    document, _, opener = source_pdf
    document.scan_status = "pending"
    with pytest.raises(ValueError, match="Source must complete malware scanning"):
        reader(document)
    opener.assert_not_called()


def test_source_type_gate_precedes_storage(source_pdf):
    document, _, opener = source_pdf
    document.declared_media_type = "text/plain"
    with pytest.raises(ValueError, match="Choose a PDF, PNG or JPEG"):
        source_documents.read_source(document)
    opener.assert_not_called()


def test_original_missing_error_is_retained_outside_local_mode(source_pdf, settings):
    document, _, opener = source_pdf
    opener.side_effect = FileNotFoundError
    settings.DEBUG = False
    with pytest.raises(ValueError, match="Original source is missing; restore the source document first"):
        source_documents.read_source(document)


def test_image_count_verifies_source_and_render_only_accepts_page_one(source_pdf):
    document, _, opener = source_pdf
    buffer = io.BytesIO()
    with Image.new("RGB", (8, 8), "white") as image:
        image.save(buffer, format="PNG")
    data = buffer.getvalue()
    document.declared_media_type = "image/png"
    document.byte_length = len(data)
    document.sha256 = hashlib.sha256(data).hexdigest()
    opener.side_effect = lambda *args: io.BytesIO(data)
    assert layout_pages.document_page_count(document) == 1
    assert source_documents.render_source_pages(document, [1])[0][0] == 1
    with pytest.raises(ValueError, match="An image source has only page 1"):
        source_documents.render_source_pages(document, [2])


def test_facade_renderer_method_remains_patchable(source_pdf, monkeypatch):
    document, _, _ = source_pdf
    render = Mock(return_value=SimpleNamespace(content=b"rendered"))
    monkeypatch.setattr(source_page_additions.FloorPlanImageExtractor, "_render_pdf_crop", render)
    assert source_documents.render_source_pages(document, [2]) == [(2, b"rendered")]
    render.assert_called_once()