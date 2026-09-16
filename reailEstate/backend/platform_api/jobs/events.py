"""Durable event names and adapter selectors, independent of worker/ORM imports.

Documents selects whole-document ingestion only. Media selects page classification,
crop evaluation and units splitting. All remains unfiltered, including future events.
Workers and other adapters can adopt these contracts without importing each other.
"""

DOCUMENT_EVENT = "document.processing.requested"
CROP_EVENT = "floor_plan.crop_evaluation.requested"
PAGE_EVENT = "source.page_evaluation.requested"
UNITS_EVENT = "floor_plan.units_evaluation.requested"
RECOMMENDATION_EVENT = "recommendation.computation.requested"

MEDIA_EVENTS = (CROP_EVENT, PAGE_EVENT, UNITS_EVENT)
EXTRACTION_EVENTS = (DOCUMENT_EVENT, *MEDIA_EVENTS)

OUTBOX_EVENT_GROUPS = {
    "all": None,
    "documents": (DOCUMENT_EVENT,),
    "recommendations": (RECOMMENDATION_EVENT,),
    "media": MEDIA_EVENTS,
    "extraction": EXTRACTION_EVENTS,
}