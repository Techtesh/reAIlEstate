import io
import socket
from pathlib import Path
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import NoReverseMatch, Resolver404, clear_url_caches, resolve, reverse

from platform_api.integrations import RejectingScanner, get_document_scanner
from realestate_common.malware import ClamAvEndpointScanner


@pytest.mark.parametrize(
    ("urlconf", "available", "hidden"),
    [
        ("admin_service.urls", "/api/v1/admin/integration-health", "/api/v1/builder/projects"),
        ("builder_service.urls", "/api/v1/builder/projects", "/api/v1/admin/integration-health"),
        ("buyer_service.urls", "/api/v1/projects/", "/api/v1/builder/projects"),
    ],
)
def test_service_url_surfaces_are_perforated(urlconf, available, hidden):
    assert resolve(available, urlconf=urlconf)
    with pytest.raises(Resolver404):
        resolve(hidden, urlconf=urlconf)


def test_only_admin_service_owns_schema_migrations():
    from admin_service import settings as settings_admin
    from builder_service import settings as settings_builder
    from buyer_service import settings as settings_buyer

    assert settings_admin.SCHEMA_MIGRATION_OWNER is True
    assert settings_builder.SCHEMA_MIGRATION_OWNER is False
    assert settings_buyer.SCHEMA_MIGRATION_OWNER is False


def test_default_workspace_data_root_survives_common_project_move():
    from config import settings as base_settings

    repository_root = Path(__file__).resolve().parents[5]
    assert base_settings.DEFAULT_WORKSPACE_DATA_ROOT == repository_root


@pytest.mark.parametrize(
    ("name", "kwargs", "buyer_exposes"),
    [
        ("project-extraction-output", {"project_id": uuid4()}, False),
        ("ingestion-output-download", {"run_id": uuid4(), "output_name": "project"}, False),
        ("project-picture", {"project_id": uuid4()}, True),
    ],
)
def test_admin_service_exposes_admin_page_read_routes(name, kwargs, buyer_exposes):
    assert reverse(name, kwargs=kwargs, urlconf="admin_service.urls")
    if buyer_exposes:
        assert reverse(name, kwargs=kwargs, urlconf="buyer_service.urls")
    else:
        with pytest.raises(NoReverseMatch):
            reverse(name, kwargs=kwargs, urlconf="buyer_service.urls")


def test_buyer_frontend_csp_allows_its_static_bundle(client, settings, tmp_path):
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    settings.ROOT_URLCONF = "buyer_service.urls"
    settings.FRONTEND_DIST = tmp_path
    clear_url_caches()

    response = client.get("/")

    assert response.status_code == 200
    assert "script-src 'self'" in response["Content-Security-Policy"]
    assert "style-src 'self'" in response["Content-Security-Policy"]


def test_non_admin_service_refuses_migrations(settings):
    settings.SCHEMA_MIGRATION_OWNER = False

    with pytest.raises(CommandError, match="Only the admin service"):
        call_command("migrate", verbosity=0)


def test_clamav_endpoint_is_disabled_by_default(settings):
    settings.CLAMAV_ENABLED = False
    settings.DOCUMENT_SCANNER = "unavailable"

    assert isinstance(get_document_scanner(), RejectingScanner)


def test_clamav_endpoint_protocol(monkeypatch):
    sent = bytearray()

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def settimeout(self, _):
            return None

        def sendall(self, value):
            sent.extend(value)

        def recv(self, _):
            return b"stream: OK\0"

    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: Connection())
    source = io.BytesIO(b"safe")

    assert ClamAvEndpointScanner("tcp://clamav:3310").scan(source) == "clean"
    assert source.tell() == 0
    assert sent.startswith(b"zINSTREAM\0")
