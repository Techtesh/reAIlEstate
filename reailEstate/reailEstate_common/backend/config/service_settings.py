"""Role-specific overlays on the common Django settings."""

from .settings import PROJECT_ROOT, REST_FRAMEWORK


def service_settings(role):
    if role not in {"admin", "builder", "buyer"}:
        raise ValueError(f"Unknown service role: {role}")
    package = f"{role}_service"
    settings = {
        "SERVICE_ROLE": role,
        "SCHEMA_MIGRATION_OWNER": role == "admin",
        "ROOT_URLCONF": f"{package}.urls",
        "WSGI_APPLICATION": f"{package}.wsgi.application",
        "ASGI_APPLICATION": f"{package}.asgi.application",
        "OTEL_SERVICE_NAME": f"mmr-real-estate-{role}",
    }
    if role == "admin":
        settings["REST_FRAMEWORK"] = {**REST_FRAMEWORK, "DEFAULT_AUTHENTICATION_CLASSES": ["platform_api.authentication.AdminSessionAuthentication"]}
    dist = PROJECT_ROOT / "reailEstate_common" / "frontend" / "dist" / ("builder" if role == "admin" else role)
    settings.update(FRONTEND_DIST=dist, STATICFILES_DIRS=[dist] if dist.is_dir() else [])
    if role != "admin":
        settings["WHITENOISE_ROOT"] = dist
    return settings
