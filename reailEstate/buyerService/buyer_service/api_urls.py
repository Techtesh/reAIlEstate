from django.urls import include, path
from rest_framework.routers import DefaultRouter

from platform_api.commute_locations_views import CommuteLocationResolveView, CommuteLocationSearchView
from platform_api.views import (
    AccountDeleteView,
    AccountExportView,
    BuyerAmenityListView,
    ComparisonShareViewSet,
    ConsentViewSet,
    CurrentUserView,
    MapStationListView,
    NotificationSubscriptionViewSet,
    PlaceSuggestView,
    ProjectComparisonView,
    ProjectGalleryView,
    ProjectPictureView,
    ProjectViewSet,
    RecommendationRequestViewSet,
    RequirementProfileViewSet,
    RequirementShareRevokeView,
    RequirementShareView,
    RequirementSubmitView,
    RequirementVersionListView,
    SessionLoginView,
    SessionLogoutView,
    SharedComparisonView,
    SharedRequirementView,
    ShortlistProjectView,
    ShortlistViewSet,
    TravelPreviewView,
)

router = DefaultRouter()
router.register("consents", ConsentViewSet, basename="consent")
router.register("requirement-profiles", RequirementProfileViewSet, basename="requirement-profile")
router.register("recommendation-requests", RecommendationRequestViewSet, basename="recommendation-request")
router.register("projects", ProjectViewSet, basename="project")
router.register("shortlists", ShortlistViewSet, basename="shortlist")
router.register("comparison-shares", ComparisonShareViewSet, basename="comparison-share")
router.register("notification-subscriptions", NotificationSubscriptionViewSet, basename="notification-subscription")

urlpatterns = [
    path("", include(router.urls)),
    path("auth/login", SessionLoginView.as_view()),
    path("auth/logout", SessionLogoutView.as_view()),
    path("auth/me", CurrentUserView.as_view()),
    path("amenities", BuyerAmenityListView.as_view()),
    path("map-stations", MapStationListView.as_view()),
    path("commute-locations/search", CommuteLocationSearchView.as_view()),
    path("commute-locations/resolve", CommuteLocationResolveView.as_view()),
    path("projects/<uuid:project_id>/gallery", ProjectGalleryView.as_view(), name="project-gallery"),
    path("projects/<uuid:project_id>/picture", ProjectPictureView.as_view(), name="project-picture"),
    path("requirement-profiles/<uuid:profile_id>:submit", RequirementSubmitView.as_view()),
    path("requirement-profiles/<uuid:profile_id>/versions", RequirementVersionListView.as_view()),
    path("requirement-profiles/<uuid:profile_id>:share", RequirementShareView.as_view()),
    path(
        "requirement-profiles/<uuid:profile_id>/shares/<uuid:share_id>",
        RequirementShareRevokeView.as_view(),
    ),
    path("shared/requirement-profiles/<str:token>", SharedRequirementView.as_view()),
    path("shared/comparisons/<str:token>", SharedComparisonView.as_view()),
    path("project-comparisons", ProjectComparisonView.as_view()),
    path("shortlists/<uuid:shortlist_id>/projects", ShortlistProjectView.as_view()),
    path("places:suggest", PlaceSuggestView.as_view()),
    path("travel-estimates:preview", TravelPreviewView.as_view()),
    path("account:export", AccountExportView.as_view()),
    path("account:delete", AccountDeleteView.as_view()),
]
