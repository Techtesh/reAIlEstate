from django.conf import settings
from django.http import FileResponse, HttpResponse


def index(request):
    path = settings.FRONTEND_DIST / "index.html"
    if not path.is_file():
        return HttpResponse(
            "Frontend is not built. Run the service frontend build.",
            status=503,
            content_type="text/plain",
        )
    return FileResponse(path.open("rb"), content_type="text/html")
