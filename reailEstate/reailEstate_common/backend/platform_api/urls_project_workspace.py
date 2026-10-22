from django.urls import path
from platform_api.project_workspace_content import ProjectContentView, ProjectCropCartView

from platform_api.builder_media import (
    BuilderActionsView,
    BuilderAssetView,
    BuilderCropView,
    BuilderDraftActionView,
    BuilderDraftPreviewView,
    BuilderEvaluateView,
    BuilderMediaView,
    BuilderPagesView,
    BuilderPageView,
    BuilderPictureView,
)
from platform_api.builder_views import (
    BuilderLayoutsView,
    BuilderOrganizationsView,
    BuilderProjectArchiveView,
    BuilderProjectsView,
    BuilderProjectView,
)
from platform_api.output_views import IngestionOutputDownloadView, ProjectExtractionOutputView
from platform_api.views import (
    CurrentUserView,
    DeveloperDocumentListView,
    DeveloperSubmitReviewView,
    DocumentCompleteView,
    DocumentContentUploadView,
    MarkDocumentLayoutPageView,
    OperationView,
    ProjectFloorPlanListView,
    ProjectIngestionQueueView,
    ProjectIngestionRunListView,
    ProjectKnowledgeSearchView,
    ProjectReanalysisView,
    ProjectVisionProfileListView,
    ReprocessDeveloperDocumentView,
    SessionLogoutView,
    UploadIntentView,
)

from platform_api.builder_views import BuilderView
from platform_api.project_workspace import AdminProjectWorkspacePermission, AdminLayoutWorkspacePermission


def project_workspace_urls(*, admin=False):
    def view(cls):
        if not admin:
            return cls.as_view()
        permissions = AdminLayoutWorkspacePermission if cls in {
            BuilderLayoutsView, BuilderActionsView, BuilderCropView, BuilderDraftActionView,
            BuilderEvaluateView, BuilderPagesView, MarkDocumentLayoutPageView,
        } else AdminProjectWorkspacePermission
        options = {"permission_classes": [permissions]}
        if issubclass(cls, BuilderView):
            options["workspace"] = "admin"
        if cls is UploadIntentView:
            options["upload_prefix"] = "/api/v1/admin/project-workspace"
        return cls.as_view(**options)

    return [
        path("builder/crop-cart", view(ProjectCropCartView)),
        path("builder/projects/<uuid:project_id>/content", view(ProjectContentView)),
        path("builder/organizations", view(BuilderOrganizationsView)),
        path("builder/projects", view(BuilderProjectsView)),
        path("builder/projects/<uuid:project_id>", view(BuilderProjectView)),
        path("builder/projects/<uuid:project_id>:archive", view(BuilderProjectArchiveView)),
        path("builder/projects/<uuid:project_id>/layouts", view(BuilderLayoutsView)),
        path("builder/projects/<uuid:project_id>/layouts/<uuid:layout_id>", view(BuilderLayoutsView)),
        path("builder/projects/<uuid:project_id>/media/picture", view(BuilderPictureView)),
        path("builder/projects/<uuid:project_id>/media", view(BuilderMediaView)),
        path("builder/projects/<uuid:project_id>/media/actions", view(BuilderActionsView)),
        path("builder/projects/<uuid:project_id>/media/assets/<uuid:asset_id>", view(BuilderAssetView)),
        path(
            "builder/projects/<uuid:project_id>/media/documents/<uuid:document_id>/pages/<int:page_number>",
            view(BuilderPageView),
        ),
        path("builder/projects/<uuid:project_id>/media/drafts/<uuid:draft_id>", view(BuilderDraftPreviewView)),
        path("builder/projects/<uuid:project_id>/media/crops", view(BuilderCropView)),
        path("builder/projects/<uuid:project_id>/media/drafts", view(BuilderDraftActionView)),
        path("builder/projects/<uuid:project_id>/media/evaluate", view(BuilderEvaluateView)),
        path("builder/projects/<uuid:project_id>/media/pages", view(BuilderPagesView)),
        path("developer-projects/<uuid:project_id>/documents:upload-intent", view(UploadIntentView)),
        path("developer-projects/<uuid:project_id>/documents", view(DeveloperDocumentListView)),
        path("developer-projects/<uuid:project_id>/ingestion-queue", view(ProjectIngestionQueueView)),
        path("developer-projects/<uuid:project_id>/vision-profiles", view(ProjectVisionProfileListView)),
        path("developer-projects/<uuid:project_id>/ingestion-runs", view(ProjectIngestionRunListView)),
        path(
            "developer-projects/<uuid:project_id>/extraction-output",
            view(ProjectExtractionOutputView),
            name="project-extraction-output",
        ),
        path(
            "ingestion-runs/<uuid:run_id>/outputs/<slug:output_name>",
            view(IngestionOutputDownloadView),
            name="ingestion-output-download",
        ),
        path("developer-projects/<uuid:project_id>/knowledge:search", view(ProjectKnowledgeSearchView)),
        path("developer-projects/<uuid:project_id>/floor-plans", view(ProjectFloorPlanListView)),
        path(
            "developer-projects/<uuid:project_id>/documents/<uuid:document_id>/content",
            view(DocumentContentUploadView),
        ),
        path(
            "developer-projects/<uuid:project_id>/documents/<uuid:document_id>:complete",
            view(DocumentCompleteView),
        ),
        path(
            "developer-projects/<uuid:project_id>/documents/<uuid:document_id>:reprocess",
            view(ReprocessDeveloperDocumentView),
        ),
        path(
            "developer-projects/<uuid:project_id>/documents/<uuid:document_id>:mark-layout-page",
            view(MarkDocumentLayoutPageView),
        ),
        path("developer-projects/<uuid:project_id>:reanalyse", view(ProjectReanalysisView)),
        path("developer-projects/<uuid:project_id>:submit-review", view(DeveloperSubmitReviewView)),
        path("auth/me", view(CurrentUserView)),
        path("auth/logout", view(SessionLogoutView)),
        path("operations/<uuid:operation_id>", view(OperationView)),
    ]
