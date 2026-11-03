"""Custom administration and GET-only compatibility routes for old bookmarks.

Keep reverse names used by retained domain adapters; never invoke Django panels.
"""
from django.contrib import admin
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.urls import URLPattern, URLResolver, path, re_path
from .service_frontend import service_frontend


MODELS = {
    "user": "users", "group": "platform-roles", "organization": "organizations",
    "organizationrole": "organization-roles", "organizationmembership": "memberships",
    "project": "projects", "floorplan": "floor-plans", "floorplanasset": "assets",
    "evidencedocument": "documents", "ingestionrun": "runs", "projectmerge": "merges",
    "projectamenity": "project-amenities", "amenity": "amenities", "stationcandidate": "stations",
    "factcandidate": "candidates", "projectfact": "facts", "reviewtask": "review-tasks",
    "documentlayoutpage": "pages", "auditevent": "audit", "consent": "consents",
    "requirementprofile": "requirements", "recommendationsnapshot": "recommendations",
}


def admin_frontend(request, **kwargs):
    if request.method not in {"GET", "HEAD"}:
        return HttpResponseNotAllowed(["GET", "HEAD"])
    from django.middleware.csrf import get_token
    get_token(request)
    return service_frontend(request)


def legacy_view(name):
    def view(request, **kwargs):
        if request.method not in {"GET", "HEAD"}:
            return HttpResponseNotAllowed(["GET", "HEAD"])
        from urllib.parse import urlencode
        if name in {"index", "login", "logout", "password_change", "password_change_done"}:
            return admin_frontend(request)
        if name is None:
            return admin_frontend(request)
        if name == "platform_api_project_add":
            return HttpResponseRedirect("/admin/project-workspace/?new=1")
        if name in {"platform_api_project_jobs", "platform_api_project_jobs_status"}:
            return HttpResponseRedirect("/admin/queue/")
        if name == "platform_api_project_layout_cart":
            return HttpResponseRedirect("/admin/project-workspace/?cart=1")
        if name and name.startswith("platform_api_floorplan_") and kwargs.get("object_id") and request.user.is_authenticated and request.user.is_staff and request.user.has_perm("platform_api.change_project"):
            from platform_api.models import FloorPlan
            from platform_api.project_workspace_admin import workspace_url
            from uuid import UUID
            try:
                plan_id = UUID(str(kwargs["object_id"]))
            except ValueError:
                plan_id = None
            plan = FloorPlan.objects.select_related("project").filter(pk=plan_id).first() if plan_id else None
            if plan and request.user.has_perm("platform_api.change_floorplan"):
                return HttpResponseRedirect(workspace_url(plan.project, plan=str(plan.pk),
                    section="floor-plans" if plan.plan_scope == "floor" else "layouts"))
        for model, resource in MODELS.items():
            if name and (name.startswith(f"platform_api_{model}_") or name.startswith(f"auth_{model}_")):
                suffix = "?" + urlencode({"record": kwargs["object_id"]}) if kwargs.get("object_id") else ""
                return HttpResponseRedirect(f"/admin/records/{resource}/{suffix}")
        return HttpResponseRedirect("/admin/")
    view.is_admin_frontend = True
    return view


def remap(pattern):
    if isinstance(pattern, URLResolver):
        return URLResolver(pattern.pattern, [remap(child) for child in pattern.url_patterns],
            default_kwargs=pattern.default_kwargs, app_name=pattern.app_name, namespace=pattern.namespace)
    return URLPattern(pattern.pattern, legacy_view(pattern.name), default_args=pattern.default_args, name=pattern.name)


# Names remain usable for historical links, but the callbacks only serve/redirect to React.
compatibility_urls = ([remap(pattern) for pattern in admin.site.get_urls()], "admin", "admin")
frontend_urls = [
    path("admin/project-workspace/", admin_frontend, name="admin-project-workspace"),
    re_path(r"^admin/(?:records/.*|queue/|flags/|operations/|password/)$", admin_frontend, name="admin-console"),
    path("admin/", compatibility_urls),
]
