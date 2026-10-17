"""Extract amenity claims from exactly one checksum-verified operator-added page."""

from django.db import transaction
from django.utils import timezone

from .amenity_matching import find_amenities
from .models import FactCandidate, Project, ReviewTask
from .project_outputs import AMENITIES, materialize_project_amenities
from .project_vision import SCHEMA


def evaluate_amenity_page(asset, operation, provider, data):
    from .floor_plan_vision import provider_metadata
    result = provider.analyze_project_page(data, asset.media_type, SCHEMA)
    categories = {code: category for category, terms in AMENITIES.items() for code in terms}
    sections = {category: {"values": [], "evidence": []} for category in AMENITIES}
    for fact in result["facts"][:100]:
        if not fact.get("fieldCode", "").startswith("amenities_") or not fact.get("evidenceText", "").strip():
            continue
        # Require the amenity in the cited visual evidence, not just an unsupported field value.
        for match in find_amenities(fact["evidenceText"]):
            section = sections[categories[match.code]]
            evidence = {"value": match.code, "documentId": str(asset.document_id),
                        "pageNumber": asset.page_number, "assetId": str(asset.pk),
                        "evidenceText": fact["evidenceText"][:500], "source": "visual_model",
                        "reviewState": "unreviewed", "provider": provider.code,
                        "model": getattr(provider, "model", ""), "matchMethod": match.method}
            if match.code not in section["values"]:
                section["values"].append(match.code)
            if evidence not in section["evidence"]:
                section["evidence"].append(evidence)
    output = {"amenities": sections, "pageNumber": asset.page_number, "assetId": str(asset.pk),
              "reviewState": "unreviewed"}
    with transaction.atomic():
        operation.refresh_from_db()
        project = Project.objects.select_for_update().get(pk=asset.project_id)
        if operation.state in {"completed", "cancelled"}:
            return
        if project.status in {"retired", "merged"}:
            raise ValueError("PROJECT_NOT_ACTIVE")
        claims = materialize_project_amenities(project, output)
        codes = sorted({code for section in sections.values() for code in section["values"]})
        if codes:
            candidate = FactCandidate.objects.create(
                project=project, document=asset.document, field_code="amenities", typed_value=codes,
                page_number=asset.page_number, bounding_region={"assetId": str(asset.pk)},
                source_class="developer", confidence=0, extractor_version="amenity-page-1.0",
                review_state="unreviewed",
            )
            ReviewTask.objects.create(project=project, candidate=candidate, task_type="fact_review", priority=2)
        workflow = asset.workflow_run
        workflow.status = "completed"
        workflow.completed_at = timezone.now()
        workflow.result = workflow.result | {"amenityPageOutput": output, "rawVisualResult": result}
        workflow.save()
        run = workflow.ingestion_run
        # A supplemental page must not replace the latest whole-document output.
        run.summary = run.summary | {"amenityPageOutput": output}
        run.save(update_fields=["summary", "updated_at"])
        operation.state = "completed"
        operation.error_code = ""
        operation.result = operation.result | {"amenityCodes": codes, "claimCount": len(claims),
                                              "outcome": "needs_review" if codes else "no_amenities_detected",
                                              **provider_metadata(provider)}
        operation.save()
