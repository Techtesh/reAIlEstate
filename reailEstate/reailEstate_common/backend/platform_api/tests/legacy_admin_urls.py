"""Unit-test harness for retained adapters, never a production URL configuration.

The console calls extracted domain services; old adapter tests still protect the
remaining adapters while they are retired. Live routing is covered separately by
test_admin_console and test_admin_project_workspace.
"""
import pytest
from django.contrib import admin
from django.urls import include, path
from config.service_frontend import admin_project_workspace

urlpatterns = [
    path("admin/project-workspace/", admin.site.admin_view(admin_project_workspace), name="admin-project-workspace"),
    path("admin/", admin.site.urls),
    path("api/v1/", include("platform_api.urls")),
]


@pytest.fixture(autouse=True)
def legacy_admin_adapters(settings):
    settings.ROOT_URLCONF = __name__
