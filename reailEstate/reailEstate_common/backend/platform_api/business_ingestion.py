import re
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .floor_plan_agent import FloorPlanAgent
from .amenity_matching import find_amenities
from .models import FactCandidate, FactConflict, ReviewTask
from .rag import retrieve_field_context


PATTERNS = {
    "project_name": re.compile(r"(?:project|development)\s*(?:name)?\s*[:\-]\s*([^\n\r|]{3,120})", re.IGNORECASE),
    "location": re.compile(r"(?:project\s*)?(?:address|location|locality)\s*[:\-]\s*([^\n\r|]{3,240})", re.IGNORECASE),
    "promoter_name": re.compile(r"(?:promoter|developer|builder)\s*(?:name)?\s*[:\-]\s*([^\n\r|]{3,160})", re.IGNORECASE),
    "maha_rera_id": re.compile(r"\b((?:P\d{10,12}|P[MR]\d{11,13}))\b", re.IGNORECASE),
    "configuration_types": re.compile(r"\b([1-6](?:\.5)?\s*BHK|studio)\b", re.IGNORECASE),
    "carpet_area_sqft": re.compile(r"(?:carpet\s*area|rera\s*carpet)[^\d]{0,30}(\d{2,5}(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|square\s*feet)", re.IGNORECASE),
    "price_inr": re.compile(r"(?:₹|INR|Rs\.?)[\s:]*(\d+(?:\.\d+)?)\s*(crore|cr|lakh|lac|million)?", re.IGNORECASE),
    "possession_date": re.compile(r"(?:possession|handover|completion)[^\n\r]{0,40}?((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\s-]+20\d{2}|(?:Q[1-4][\s-]+)?20\d{2})", re.IGNORECASE),
}
@dataclass(frozen=True)
class ExtractedValue:
    field_code: str
    value: object
    unit: str
    confidence: float
    page_number: int | None
    chunk_id: str


class RagFactExtractor:
    """Schema-bound extraction over retrieved evidence; brochure instructions are inert data."""

    version = "rag-fact-extractor-1.0"

    def extract(self, ingestion_run):
        contexts = retrieve_field_context(ingestion_run.project, ingestion_run)
        values = []
        values.extend(self._regex_values(contexts))
        values.extend(self._amenities(contexts.get("amenities", [])))
        return self._deduplicate(values)

    def _regex_values(self, contexts):
        values = []
        for field_code, pattern in PATTERNS.items():
            context_key = "price" if field_code == "price_inr" else field_code
            for result in contexts.get(context_key, []):
                chunk = result["chunk"]
                for match in pattern.finditer(chunk.content):
                    raw = match.group(1)
                    value, unit = self._normalize(field_code, raw, match.group(2) if match.lastindex and match.lastindex >= 2 else None)
                    values.append(ExtractedValue(
                        field_code=field_code,
                        value=value,
                        unit=unit,
                        confidence=min(0.95, 0.6 + result["score"] * 0.3),
                        page_number=chunk.page_number,
                        chunk_id=str(chunk.id),
                    ))
        return values

    def _amenities(self, contexts):
        found = []
        for result in contexts:
            chunk = result["chunk"]
            matches = sorted({match.code for match in find_amenities(chunk.content)})
            if matches:
                found.append(ExtractedValue("amenities", sorted(matches), "", min(0.9, 0.55 + result["score"] * 0.3), chunk.page_number, str(chunk.id)))
        return found

    def _normalize(self, field_code, raw, qualifier):
        if field_code in {"project_name", "location", "promoter_name", "possession_date"}:
            return raw.strip(" .,-"), ""
        if field_code == "maha_rera_id":
            return raw.upper(), ""
        if field_code == "configuration_types":
            return raw.upper().replace(" ", ""), ""
        if field_code == "carpet_area_sqft":
            return float(Decimal(raw)), "sqft"
        if field_code == "price_inr":
            multiplier = {"crore": 10_000_000, "cr": 10_000_000, "lakh": 100_000, "lac": 100_000, "million": 1_000_000}.get((qualifier or "").casefold(), 1)
            return float(Decimal(raw) * multiplier), "INR"
        return raw, ""

    @staticmethod
    def _deduplicate(values):
        selected = {}
        for value in values:
            key = (value.field_code, repr(value.value), value.page_number)
            if key not in selected or value.confidence > selected[key].confidence:
                selected[key] = value
        return list(selected.values())


@transaction.atomic
def _persist_project_facts(ingestion_run, extracted):
    # Serialize same-project reconciliation briefly, not the model calls that follow.
    type(ingestion_run.project).objects.select_for_update().get(pk=ingestion_run.project_id)
    candidates = []
    by_field = {}
    for item in extracted:
        candidate = FactCandidate.objects.create(
            project=ingestion_run.project,
            document=ingestion_run.document,
            field_code=item.field_code,
            typed_value=item.value,
            unit=item.unit,
            page_number=item.page_number,
            bounding_region={"knowledgeChunkId": item.chunk_id},
            source_class="developer",
            confidence=item.confidence,
            extractor_version=RagFactExtractor.version,
            review_state="unreviewed",
        )
        candidates.append(candidate)
        by_field.setdefault(item.field_code, []).append(candidate)

    review_count = 0
    for field_code, field_candidates in by_field.items():
        project_candidates = list(
            FactCandidate.objects.filter(project=ingestion_run.project, field_code=field_code)
            .exclude(review_state="rejected")
            .order_by("-confidence", "created_at")
        )
        unique_values = {repr(candidate.typed_value) for candidate in project_candidates}
        if len(unique_values) > 1 or any(candidate.confidence < 0.85 for candidate in field_candidates):
            conflict, _ = FactConflict.objects.get_or_create(project=ingestion_run.project, field_code=field_code, state="open")
            conflict.candidates.add(*project_candidates)
            ReviewTask.objects.get_or_create(
                project=ingestion_run.project,
                candidate=max(project_candidates, key=lambda candidate: candidate.confidence),
                task_type="conflict" if len(unique_values) > 1 else "low_confidence",
                state="open",
                defaults={"priority": 2 if field_code in {"price_inr", "carpet_area_sqft", "possession_date"} else 3},
            )
            review_count += 1
        else:
            ReviewTask.objects.get_or_create(
                project=ingestion_run.project,
                candidate=max(project_candidates, key=lambda candidate: candidate.confidence),
                task_type="fact_review",
                state="open",
                defaults={"priority": 4},
            )
            review_count += 1

    return candidates, review_count


def extract_project_knowledge(ingestion_run, content=None, media_type=None, vision_provider=None):
    extracted = RagFactExtractor().extract(ingestion_run)
    candidates, review_count = _persist_project_facts(ingestion_run, extracted)
    floor_workflow = FloorPlanAgent(vision_provider=vision_provider).run(
        ingestion_run,
        content=content,
        media_type=media_type,
    )
    ingestion_run.extraction_provider = floor_workflow.result.get("visionProvider", "unavailable")
    if not candidates and not floor_workflow.result.get("detected"):
        ReviewTask.objects.get_or_create(
            project=ingestion_run.project,
            task_type="insufficient_data",
            state="open",
            defaults={"priority": 3, "resolution": {"ingestionRunId": str(ingestion_run.id)}},
        )
        review_count += 1
    ingestion_run.summary = ingestion_run.summary | {
        "factCandidateCount": len(candidates),
        "reviewTaskCount": review_count,
        "floorPlanWorkflowId": str(floor_workflow.id),
    }
    ingestion_run.completed_at = timezone.now()
    ingestion_run.save(update_fields=["summary", "extraction_provider", "completed_at", "updated_at"])
    return {"candidates": candidates, "floorPlanWorkflow": floor_workflow}
