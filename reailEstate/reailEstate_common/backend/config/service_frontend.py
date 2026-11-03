from django.conf import settings
from django.http import FileResponse, HttpResponse


def service_frontend(request):
    dist = getattr(settings, "FRONTEND_DIST", settings.PROJECT_ROOT / "reailEstate_common" / "frontend" / "dist" / "builder")
    path = dist / "index.html"
    if not path.is_file():
        return HttpResponse(
            "Frontend is not built. Run the service frontend build.",
            status=503,
            content_type="text/plain",
        )
    return FileResponse(path.open("rb"), content_type="text/html")


def admin_project_workspace(request):
    from django.core.exceptions import PermissionDenied
    from django.middleware.csrf import get_token
    from platform_api.project_workspace import platform_project_editor

    if not platform_project_editor(request.user):
        raise PermissionDenied
    get_token(request)
    return service_frontend(request)
