from config.settings import *  # noqa: F403

SERVICE_ROLE = "admin"
SCHEMA_MIGRATION_OWNER = True
ROOT_URLCONF = "admin_service.urls"
WSGI_APPLICATION = "admin_service.wsgi.application"
ASGI_APPLICATION = "admin_service.asgi.application"
OTEL_SERVICE_NAME = "mmr-real-estate-admin"
