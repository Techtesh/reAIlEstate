import re
from dataclasses import dataclass

import pymupdf
from django.conf import settings


_POSITIVE_PATTERNS = (
    (re.compile(r"\b(?:floor|unit|flat|apartment|typical|tower|wing|site|master)\s*(?:plan|layout)s?\b|\blayout\s*plan\b", re.IGNORECASE), 7.0),
    (re.compile(r"\b[1-6](?:\.5)?\s*BHK\b|\bstudio\b", re.IGNORECASE), 3.0),
    (re.compile(r"\b(?:rera\s*)?carpet\s*area\b", re.IGNORECASE), 3.0),
    (re.compile(r"\b(?:living|bed\s*room|bedroom|kitchen|toilet|balcony|foyer)\b", re.IGNORECASE), 1.0),
    (re.compile(r"\d{1,2}\s*['’\-]\s*\d{1,2}\s*[\"”]?\s*[x×]\s*\d{1,2}", re.IGNORECASE), 2.0),
)
_INDIVIDUAL_LAYOUT_PATTERN = re.compile(
    r"\b(?:unit|flat|apartment)\s*(?:plan|layout)s?\b|"
    r"\b[1-6](?:\.5)?\s*BHK\b(?=[\s\S]{0,300}\b(?:living|bed\s*room|bedroom|kitchen|toilet|bathroom)\b)",
    re.IGNORECASE,
)
_MULTI_UNIT_FLOOR_PATTERN = re.compile(
    r"\b(?:typical\s*floor|tower\s*plan|wing\s*plan|floor\s*(?:plan|layout))\b",
    re.IGNORECASE,
)
_NEGATIVE_PATTERN = re.compile(r"\b(?:location\s*map|amenities|lifestyle|elevation|artist(?:'s)?\s*impression|specification)s?\b", re.IGNORECASE)


@dataclass(frozen=True)
class SelectedFloorPlanPage:
    page_number: int
    score: float
    reasons: tuple[str, ...]


class FloorPlanPageSelector:
    """Selects a bounded set of likely plan pages before expensive high-resolution rendering."""

    version = "floor-plan-page-selector-1.1"

    def __init__(self, maximum_pages=None, minimum_score=3.0):
        self.maximum_pages = maximum_pages or settings.FLOOR_PLAN_MAX_PAGES
        self.minimum_score = minimum_score

    def select(self, content: bytes, media_type: str) -> list[SelectedFloorPlanPage]:
        if media_type != "application/pdf":
            return [SelectedFloorPlanPage(1, 1.0, ("image_document",))]
        pymupdf.TOOLS.mupdf_display_errors(False)
        pymupdf.TOOLS.mupdf_display_warnings(False)
        document = None
        try:
            document = pymupdf.open(stream=content, filetype="pdf")
            ranked = [self._score_page(page, index) for index, page in enumerate(document, start=1)]
        finally:
            if document is not None:
                document.close()
            pymupdf.TOOLS.mupdf_display_errors(True)
            pymupdf.TOOLS.mupdf_display_warnings(True)
        selected = [item for item in ranked if item.score >= self.minimum_score]
        selected.sort(key=lambda item: (-item.score, item.page_number))
        return selected[:self.maximum_pages]

    @staticmethod
    def _score_page(page, page_number):
        text = page.get_text("text")[:100_000]
        score = 0.0
        reasons = []
        if _INDIVIDUAL_LAYOUT_PATTERN.search(text):
            score += 12.0
            reasons.append("individual_layout")
        elif _MULTI_UNIT_FLOOR_PATTERN.search(text):
            score += 4.0
            reasons.append("multi_unit_floor")
        signal_names = ("plan_heading", "configuration_label", "carpet_area_label", "room_labels", "dimension_labels")
        for (pattern, weight), signal_name in zip(_POSITIVE_PATTERNS, signal_names):
            matches = pattern.findall(text)
            if matches:
                score += min(weight * 2, weight + (len(matches) - 1) * 0.5)
                reasons.append(signal_name)
        drawing_count = len(page.get_drawings())
        image_count = len(page.get_images(full=False))
        if drawing_count >= 20:
            score += 3.0
            reasons.append("vector_drawing_density")
        elif drawing_count >= 5:
            score += 0.75
            reasons.append("vector_drawings")
        if image_count and len(text.strip()) < 120:
            score += 3.0
            reasons.append("image_with_little_text")
        room_labels = set(re.findall(r"\b(?:living|bed\s*room|bedroom|kitchen|toilet|bathroom|balcony|foyer)\b", text.casefold()))
        supported = (
            "individual_layout" in reasons
            or "multi_unit_floor" in reasons
            or "plan_heading" in reasons
            or len(room_labels) >= 3
            or (len(room_labels) >= 2 and "configuration_label" in reasons)
            or "vector_drawing_density" in reasons
            or "image_with_little_text" in reasons
        )
        if _NEGATIVE_PATTERN.search(text) and not (
            "plan_heading" in reasons or "vector_drawing_density" in reasons or len(room_labels) >= 2
        ):
            score -= 2.0
        if not supported:
            score = 0.0
        return SelectedFloorPlanPage(page_number, round(max(0.0, score), 4), tuple(reasons))
