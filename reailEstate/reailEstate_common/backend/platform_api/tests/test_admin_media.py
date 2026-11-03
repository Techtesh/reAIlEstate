from types import SimpleNamespace
from uuid import uuid4

import pytest
from .legacy_admin_urls import legacy_admin_adapters  # noqa: F401 - opt-in adapter regression harness
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse
from django.template.loader import get_template

from platform_api.admin import _asset_preview
from platform_api.middleware import CorrelationIdMiddleware


def response_policy(path, namespaces):
    request = RequestFactory().get(path)
    request.resolver_match = SimpleNamespace(namespaces=namespaces, url_name=None)
    return CorrelationIdMiddleware(lambda request: HttpResponse("OK"))(request)["Content-Security-Policy"]


def test_admin_allows_local_resources_without_inline_scripts():
    policy = response_policy("/admin/platform_api/floorplan/", ["admin"])
    assert "img-src 'self'" in policy
    assert "script-src 'self';" in policy
    assert "style-src 'self' 'unsafe-inline'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "form-action 'self'" in policy


@pytest.mark.parametrize("path", ["/api/v1/projects/", "/api/v1/media/floor-plan-assets/example", "/admin/not-a-resolved-view/"])
def test_non_admin_routes_retain_restrictive_policy(path):
    assert response_policy(path, []) == "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"


def test_image_preview_uses_authenticated_endpoint():
    asset = SimpleNamespace(id=uuid4(), page_number=27, status="ready")
    html = str(_asset_preview(asset))
    url = reverse("floor-plan-asset-media", kwargs={"asset_id": asset.id})
    assert f'src="{url}"' in html
    assert f'href="{url}"' in html
    assert 'rel="noopener"' in html
    assert "source page 27" in html


def test_missing_or_unready_image_has_no_broken_tag():
    assert "<img" not in str(_asset_preview(None))
    assert "<img" not in str(_asset_preview(SimpleNamespace(status="pending")))


def test_project_dashboard_uses_external_carousel_script():
    from pathlib import Path
    from .workspace_helpers import frontend_source
    source = frontend_source()
    assert 'function selectImage' in source
    assert 'Previous image' in source and 'Next image' in source
    assert not (Path(__file__).parents[1] / 'static/platform_api/project_media_dashboard.js').exists()


def test_source_page_picker_includes_image_theater_controls():
    from .workspace_helpers import frontend_source
    source = frontend_source()
    assert 'Open original source page in new tab' in source
    assert 'openInNewTab' in source and 'Maximize2' in source
    assert 'Crop source editor' in source

def test_project_dashboard_media_tab_order():
    from .workspace_helpers import frontend_source
    source = frontend_source().split('const tabs:',1)[1].split('const emptyRegion',1)[0]
    positions = [source.index(f"id: '{section}'") for section in ['project-images','amenities','floor-plans','layouts']]
    assert positions == sorted(positions)
