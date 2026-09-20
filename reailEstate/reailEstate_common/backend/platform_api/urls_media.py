from django.urls import path

from .views import FloorPlanAssetMediaView, MediaPageView

urlpatterns = [
    path("media/<uuid:document_id>/pages/<int:page_number>", MediaPageView.as_view(), name="media-page"),
    path(
        "media/floor-plan-assets/<uuid:asset_id>",
        FloorPlanAssetMediaView.as_view(),
        name="floor-plan-asset-media",
    ),
]
