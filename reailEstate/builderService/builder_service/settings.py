from pathlib import Path

from config.settings import *  # noqa: F403

SERVICE_ROLE = "builder"
SCHEMA_MIGRATION_OWNER = False
ROOT_URLCONF = "builder_service.urls"
WSGI_APPLICATION = "builder_service.wsgi.application"
ASGI_APPLICATION = "builder_service.asgi.application"
OTEL_SERVICE_NAME = "mmr-real-estate-builder"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = PROJECT_ROOT / "reailEstate_common" / "frontend" / "dist" / "builder"
STATICFILES_DIRS = [FRONTEND_DIST] if FRONTEND_DIST.is_dir() else []
WHITENOISE_ROOT = FRONTEND_DIST
