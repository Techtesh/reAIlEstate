from django.urls import path
from platform_api.builder_views import BuilderLoginView
from platform_api.organization_roles_views import OrganizationRolesView, OrganizationRoleMembersView
from platform_api.urls_project_workspace import project_workspace_urls
from platform_api.views import DeveloperProjectCreateView

urlpatterns = [
    path("builder/organizations/<uuid:organization_id>/roles", OrganizationRolesView.as_view()),
    path("builder/organizations/<uuid:organization_id>/roles/<uuid:role_id>/members", OrganizationRoleMembersView.as_view()),
    path("auth/builder-login", BuilderLoginView.as_view()),
    path("developer-organizations/<uuid:organization_id>/projects", DeveloperProjectCreateView.as_view()),
    *project_workspace_urls(),
]
