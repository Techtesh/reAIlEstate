"""Compatibility links to the shared crop cart."""
from django.http import HttpResponseRedirect
from .project_workspace_admin import workspace_redirect


class CropCartAdminMixin:
    def draft_crop_action(self, request, object_id):
        return workspace_redirect(self, request, object_id)

    def draft_crop_preview(self, request, object_id, draft_id):
        redirect = workspace_redirect(self, request, object_id)
        if redirect.status_code != 302:
            return redirect
        return HttpResponseRedirect(f'/api/v1/admin/project-workspace/builder/projects/{object_id}/media/drafts/{draft_id}')
