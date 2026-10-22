from django.contrib import admin
from django.urls import path
from config.service_frontend import admin_project_workspace
from config.service_urls import service_urlpatterns

urlpatterns = [
    path("admin/project-workspace/", admin.site.admin_view(admin_project_workspace), name="admin-project-workspace"),
    *service_urlpatterns("admin_service.api_urls", django_admin=True),
]
