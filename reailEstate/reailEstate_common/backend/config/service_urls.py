from django.contrib import admin
from django.urls import include, path, re_path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from platform_api.views import LiveHealthView, ReadyHealthView


def service_urlpatterns(api_urlconf, *, django_admin=False, frontend=False):
    patterns = [
        path("api/v1/", include(api_urlconf)),
        path("api/v1/", include("platform_api.urls_media")),
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
        path("health/live", LiveHealthView.as_view()),
        path("health/ready", ReadyHealthView.as_view()),
    ]
    if django_admin:
        from .admin_frontend_urls import frontend_urls
        patterns = [*frontend_urls, *patterns]
    if frontend:
        from .service_frontend import service_frontend

        patterns.append(re_path(
            r"^(?!api/|admin/|health/|static/).*$",
            service_frontend,
            name="service-frontend",
        ))
    return patterns
