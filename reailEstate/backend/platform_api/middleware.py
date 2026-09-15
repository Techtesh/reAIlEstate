import uuid

from django.conf import settings
from django.http import JsonResponse


class LocalMaintenanceLockMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        lock = settings.WORKSPACE_DATA_ROOT / ".script-rebuild.lock"
        if settings.DEBUG and lock.exists() and request.path not in {"/health/live", "/health/ready"}:
            response = JsonResponse({"code": "LOCAL_MAINTENANCE", "detail": "Local extraction rebuild in progress."}, status=503)
            response["Retry-After"] = "60"
            return response
        return self.get_response(request)


class CorrelationIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        supplied = request.headers.get("X-Correlation-ID", "")
        try:
            request.correlation_id = uuid.UUID(supplied) if supplied else uuid.uuid4()
        except ValueError:
            request.correlation_id = uuid.uuid4()
        response = self.get_response(request)
        response["X-Correlation-ID"] = str(request.correlation_id)
        response["X-Content-Type-Options"] = "nosniff"
        response["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(self)"
        policy = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        match = getattr(request, "resolver_match", None)
        if match and "admin" in match.namespaces:
            # Admin templates use local CSS, JS, SVG icons and inline layout styles.
            # Do not apply these exceptions to API or uploaded media responses.
            policy += (
                "; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self'; font-src 'self'; connect-src 'self'; form-action 'self'"
            )
        response["Content-Security-Policy"] = policy
        return response
