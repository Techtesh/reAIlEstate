from pathlib import Path

from config.settings import *  # noqa: F403

SERVICE_ROLE = "buyer"
SCHEMA_MIGRATION_OWNER = False
ROOT_URLCONF = "buyer_service.urls"
WSGI_APPLICATION = "buyer_service.wsgi.application"
ASGI_APPLICATION = "buyer_service.asgi.application"
OTEL_SERVICE_NAME = "mmr-real-estate-buyer"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = PROJECT_ROOT / "reailEstate_common" / "frontend" / "dist" / "buyer"
STATICFILES_DIRS = [FRONTEND_DIST] if FRONTEND_DIST.is_dir() else []
WHITENOISE_ROOT = FRONTEND_DIST
