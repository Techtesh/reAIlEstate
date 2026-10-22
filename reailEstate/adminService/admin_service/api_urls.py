from django.urls import include, path
from rest_framework.routers import DefaultRouter

from platform_api.output_views import IngestionOutputDownloadView, ProjectExtractionOutputView
from platform_api.views import (
    AmenityAdminViewSet,
    AuditEventAdminView,
    FeatureFlagAdminViewSet,
    IntegrationHealthAdminView,
    ManualFloorPlanCropsAdminView,
    OperationalMetricsAdminView,
    OperationView,
    OrganizationViewSet,
    ProjectPictureView,
    ProjectMergeAdminView,
    PublishProjectView,
    ReplayOutboxAdminView,
    ReprocessDocumentAdminView,
    ResolveFloorPlanView,
    ResolveReviewTaskView,
    ReviewTaskViewSet,
    SegmentFloorPlanUnitsAdminView,
)

router = DefaultRouter()
router.register("developer-organizations", OrganizationViewSet, basename="organization")
router.register("review/tasks", ReviewTaskViewSet, basename="review-task")
router.register("admin/amenities", AmenityAdminViewSet, basename="admin-amenity")
router.register("admin/feature-flags", FeatureFlagAdminViewSet, basename="admin-feature-flag")

from platform_api.urls_project_workspace import project_workspace_urls

urlpatterns = [
    path("admin/project-workspace/", include(project_workspace_urls(admin=True))),
    path("", include(router.urls)),
    path(
        "developer-projects/<uuid:project_id>/extraction-output",
        ProjectExtractionOutputView.as_view(),
        name="project-extraction-output",
    ),
    path(
        "ingestion-runs/<uuid:run_id>/outputs/<slug:output_name>",
        IngestionOutputDownloadView.as_view(),
        name="ingestion-output-download",
    ),
    path("projects/<uuid:project_id>/picture", ProjectPictureView.as_view(), name="project-picture"),
    path("review/tasks/<uuid:task_id>:resolve", ResolveReviewTaskView.as_view()),
    path("review/floor-plans/<uuid:floor_plan_id>:resolve", ResolveFloorPlanView.as_view()),
    path("review/projects/<uuid:project_id>:publish", PublishProjectView.as_view()),
    path("operations/<uuid:operation_id>", OperationView.as_view()),
    path("admin/integration-health", IntegrationHealthAdminView.as_view()),
    path("admin/operational-metrics", OperationalMetricsAdminView.as_view()),
    path("admin/audit-events", AuditEventAdminView.as_view()),
    path(
        "admin/floor-plan-segmentation",
        SegmentFloorPlanUnitsAdminView.as_view(),
        name="admin-floor-plan-segmentation-profiles",
    ),
    path(
        "admin/floor-plans/<uuid:floor_plan_id>:segment-units",
        SegmentFloorPlanUnitsAdminView.as_view(),
        name="admin-floor-plan-segment-units",
    ),
    path(
        "admin/floor-plans/<uuid:floor_plan_id>:manual-crops",
        ManualFloorPlanCropsAdminView.as_view(),
        name="admin-floor-plan-manual-crops",
    ),
    path("admin/projects/<uuid:project_id>:merge", ProjectMergeAdminView.as_view(), name="admin-project-merge"),
    path("admin/documents/<uuid:document_id>:reprocess", ReprocessDocumentAdminView.as_view()),
    path("admin/outbox/<uuid:message_id>:replay", ReplayOutboxAdminView.as_view()),
]
