"""Folder-wide evidence reports: union sources, retain conflicts, never sum duplicated claims."""

import json
from collections import defaultdict

from .models import IngestionRun
from .project_outputs import field


def _unique(items):
    return list({json.dumps(item, sort_keys=True): item for item in items}.values())


def aggregate_outputs(outputs):
    fields = defaultdict(list)
    amenities = defaultdict(list)
    images, registrations, towers = [], [], []

    def collect(value, path=""):
        if isinstance(value, dict):
            if "candidates" in value and "status" in value:
                fields[path].extend(value["candidates"])
                return
            for key, child in value.items():
                collect(child, f"{path}.{key}" if path else key)

    for output in outputs:
        collect(output)
        images.extend(output.get("imageEvidence", []))
        registrations.extend(output.get("rera", {}).get("evidence", []))
        towers.extend(output.get("parcelDetails", {}).get("towerUnitsAndLifts", []))
        for category, value in output.get("amenities", {}).items():
            amenities[category].extend(value.get("evidence", []))
    return {"fields": {key: field(_unique(items)) for key, items in fields.items()},
            "amenities": {key: {"values": sorted({item["value"] for item in items}), "evidence": _unique(items)}
                          for key, items in amenities.items()},
            "rera": {"numbers": sorted({item["value"] for item in registrations}), "evidence": _unique(registrations)},
            "towerClaims": _unique(towers), "imageEvidence": _unique(images)}


def build_group_report(project, key, rows):
    # Only include runs explicitly represented in this corpus pass; exclude stale/failed outputs.
    run_ids = {row["ingestionRunId"] for row in rows if row.get("ingestionRunId")
               and row["state"] in {"completed", "awaiting_visual_analysis"}}
    runs = list(IngestionRun.objects.filter(project=project, id__in=run_ids).select_related("document").order_by("created_at"))
    outputs = [run.summary["projectOutput"] for run in runs if run.summary.get("projectOutput")]
    return {"schemaVersion": "sample-project-evidence-1.0", "projectId": str(project.id), "projectKey": key,
            "name": project.address.get("sampleCorpusName", project.submitted_name), "locality": project.locality,
            "reviewState": "unreviewed", "sources": rows, "combinedEvidence": aggregate_outputs(outputs),
            "documentOutputs": outputs,
            "textEvidence": [{"documentId": str(run.document_id), "ingestionRunId": str(run.id),
                              "originalName": run.document.original_name,
                              "chunks": list(run.chunks.order_by("ordinal").values("ordinal", "page_number", "content", "metadata"))}
                             for run in runs],
            "limitations": ["Folder membership groups sources; it does not prove that all claims concern one phase or tower.",
                            "Different values remain conflicts with source provenance; totals are never added across documents.",
                            "Identical files within one folder share one extraction and retain every source path.",
                            "Original sources and earlier extraction runs are preserved; all new claims require review.",
                            "Text chunks retain supplementary details even where structured extraction has no matching field."]}