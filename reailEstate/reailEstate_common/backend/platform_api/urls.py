"""Compatibility URL surface for local development and incremental migration."""

from django.urls import include, path

urlpatterns = [
    path("", include("admin_service.api_urls")),
    path("", include("builder_service.api_urls")),
    path("", include("buyer_service.api_urls")),
    path("", include("platform_api.urls_media")),
]
