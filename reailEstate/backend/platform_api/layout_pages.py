"""Validation helpers for explicit document page decisions."""

from .infrastructure.source_documents import source_page_count


def document_page_count(document):
    """Count verified source pages, never an incomplete extraction's chunk maximum."""
    return source_page_count(document)


def validate_document_page(document, page_number):
    if not 1 <= page_number <= document_page_count(document):
        raise ValueError("PAGE_NUMBER_OUT_OF_RANGE")
    return page_number
