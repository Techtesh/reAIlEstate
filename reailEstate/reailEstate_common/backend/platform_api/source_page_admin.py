"""Protected source-document readers and a compatibility link to the shared editor."""

import io

from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse

from .policies.crops import authorize_crops
from .project_workspace_admin import workspace_redirect
from .models import EvidenceDocument
from .project_lifecycle import project_family_ids
from .source_page_additions import read_source, render_source_pages


class SourcePageAdminMixin:
    def source_document_view(self, request, object_id, document_id):
        project = self._source_project(request, object_id)
        try:
            document = EvidenceDocument.objects.get(id=document_id, project_id__in=project_family_ids(project))
            data = read_source(document)
        except (EvidenceDocument.DoesNotExist, ValueError, OSError):
            raise Http404("Source document unavailable") from None
        response = FileResponse(io.BytesIO(data), content_type=document.declared_media_type,
                                filename=document.original_name, as_attachment=False)
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    def _source_project(self, request, object_id):
        project = self.get_object(request, object_id)
        if project is None:
            raise Http404
        if not self.has_change_permission(request, project):
            raise PermissionDenied
        return project

    def add_source_pages_view(self, request, object_id):
        return workspace_redirect(self, request, object_id)


    def source_preview_view(self, request, object_id, document_id, page_number):
        project = self._source_project(request, object_id)
        crop_preview = request.GET.get("category") == "layouts" or "source_sha256" in request.GET
        try:
            if crop_preview:
                authorize_crops(request.user, project)
            document = EvidenceDocument.objects.get(id=document_id, project_id__in=project_family_ids(project))
            if crop_preview and request.GET.get("source_sha256") != document.sha256:
                raise ValueError("Source changed; reload the cropper.")
            data = render_source_pages(document, [page_number])[0][1]
        except (EvidenceDocument.DoesNotExist, ValueError, OSError):
            raise Http404("Source page unavailable") from None
        response = HttpResponse(data, content_type="image/png")
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response
