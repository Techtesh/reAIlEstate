"""Compatibility URLs for the retired admin crop editor."""
from django.http import HttpResponseGone
from django.urls import path
from .project_workspace_admin import workspace_redirect


class LayoutCropUpdateAdminMixin:
    def layout_crop_update_urls(self):
        return [
            path('<uuid:object_id>/edit-crop/', self.admin_site.admin_view(self.layout_crop_update_view),
                 name='platform_api_floorplan_crop_update'),
            path('<uuid:object_id>/edit-crop/preview/', self.admin_site.admin_view(self.layout_crop_update_preview),
                 name='platform_api_floorplan_crop_update_preview'),
        ]

    def layout_crop_update_view(self, request, object_id):
        return workspace_redirect(self, request, object_id)

    def layout_crop_update_preview(self, request, object_id):
        response = workspace_redirect(self, request, object_id)
        return HttpResponseGone('Open the project workspace to preview and edit crops.') if response.status_code == 302 else response
