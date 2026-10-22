import csv
import io
import json

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .artifact_storage import storage
from .models import FloorPlan, IngestionRun, Project
from .floor_plan_visibility import HIDDEN_REVIEW_STATES
from .sample_reports import aggregate_outputs
from .services import audit


def check_output_access(user, project):
    from .views import _project_can_edit, _is_reviewer
    if not _project_can_edit(user, project) and not _is_reviewer(user):
        raise PermissionDenied()


class ProjectExtractionOutputView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = get_object_or_404(Project, id=project_id)
        check_output_access(request.user, project)
        runs = project.ingestion_runs.filter(
            status__in=["completed", "needs_provider"], summary__has_key="projectOutput",
        ).order_by("-created_at")
        documents, seen = [], set()
        # Preserve source boundaries rather than mixing phases or resolving conflicts implicitly.
        for run in runs.iterator():
            if run.document_id in seen:
                continue
            seen.add(run.document_id)
            documents.append({"ingestionRunId": str(run.id), "documentId": str(run.document_id),
                "status": run.status, "data": run.summary["projectOutput"],
                "outputs": {name: item["uri"] for name, item in run.summary.get("outputs", {}).items()}})
            if len(documents) == 100:
                break
        response = Response({"projectId": str(project.id), "documents": documents,
            "combinedEvidence": aggregate_outputs([item["data"] for item in documents]),
            "scope": "project_with_latest_output_per_document", "limit": 100,
            "note": "All project documents contribute evidence. Conflicts and registration phases require review; document totals are not automatically added."})
        response["Cache-Control"] = "private, no-store"
        return response


class IngestionOutputDownloadView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, run_id, output_name):
        run = get_object_or_404(IngestionRun.objects.select_related("project"), id=run_id,
                               status__in=["completed", "needs_provider"])
        check_output_access(request.user, run.project)
        types = {"project": ("application/json", "json"), "layout-metrics": ("application/json", "json"),
                 "layout-metrics-csv": ("text/csv", "csv")}
        if output_name not in types:
            raise NotFound()
        key = run.summary.get("outputs", {}).get(output_name, {}).get("objectKey", "")
        if not key.startswith(f"output/media/projects/{run.project_id}/{run.id}/") or not storage.exists(key):
            raise NotFound()
        media_type, suffix = types[output_name]
        hidden_ids = {str(pk) for pk in FloorPlan.objects.filter(
            project=run.project, review_state__in=HIDDEN_REVIEW_STATES
        ).values_list("pk", flat=True)} if output_name.startswith("layout-metrics") else set()
        if hidden_ids:
            # Filter at download time as well: historical artifacts are immutable and
            # may predate the user's removal. Never rewrite or delete those artifacts.
            with storage.open(key, "rb") as source:
                if output_name == "layout-metrics":
                    data = json.load(source)
                    data["layouts"] = [row for row in data.get("layouts", [])
                                       if row.get("floorPlanId") not in hidden_ids]
                    stream = io.BytesIO(json.dumps(data, indent=2, allow_nan=False).encode("utf-8"))
                else:
                    reader = csv.DictReader(io.StringIO(source.read().decode("utf-8-sig")))
                    buffer = io.StringIO(newline="")
                    writer = csv.DictWriter(buffer, fieldnames=reader.fieldnames or [])
                    writer.writeheader()
                    writer.writerows(row for row in reader if row.get("floorPlanId") not in hidden_ids)
                    stream = io.BytesIO(buffer.getvalue().encode("utf-8-sig"))
        else:
            stream = storage.open(key)
        response = FileResponse(stream, content_type=media_type,
                                as_attachment=True, filename=f"{output_name}-{run.id}.{suffix}")
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        audit(request, "ingestion.output_downloaded", run, metadata={"outputName": output_name})
        return response