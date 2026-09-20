"""Leaf dependency and compatibility contracts; no database access is needed."""

import importlib
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("name", [
    "MAX_UNITS", "MIN_BOX_AREA", "MAX_BOX_AREA", "UnitRegion",
    "floor_applicability", "with_floor_applicability", "validate_regions",
    "_content_mask", "_red_markers", "_dominant_markers", "_regions_from_seeds",
    "_radial_seeds", "_colored_regions", "segment_units_script", "crop_region",
])
def test_geometry_compatibility_exports_are_identical(name):
    from platform_api import unit_segmentation
    from platform_api.domain import geometry

    assert getattr(unit_segmentation, name) is getattr(geometry, name)


@pytest.mark.parametrize("name", [
    "DISPLAY_NAME_MAX_LENGTH", "_UUID", "_GENERATED_SUFFIX", "_BHK",
    "_UNSAFE_CATEGORIES", "_safe_text", "clean_layout_name",
    "layout_display_title", "validate_layout_display_name",
])
def test_title_compatibility_exports_are_identical(name):
    from platform_api import layout_names
    from platform_api.domain import layout_titles

    assert getattr(layout_names, name) is getattr(layout_titles, name)


def test_crop_policy_compatibility_exports_are_identical():
    from platform_api import crop_cart, crop_from_page, layout_crop_update
    from platform_api.policies import crops

    assert crop_cart.CART_LIMIT is crop_from_page.CART_LIMIT is crops.CART_LIMIT
    assert crop_cart.MAX_DRAFT_BYTES is crop_from_page.MAX_DRAFT_BYTES is crops.MAX_DRAFT_BYTES
    assert crop_cart._authorize is crop_from_page._authorize is crops.authorize_crops
    assert crop_cart.crop_destination is layout_crop_update.crop_destination is crops.crop_destination


@pytest.mark.parametrize("module_name,function_name", [
    ("unit_segmentation", "replace_unit_segments"),
    ("crop_cart", "stage_crops"),
    ("crop_from_page", "_save_page_drafts"),
])
def test_service_crop_bindings_remain_local_patch_points(monkeypatch, module_name, function_name):
    from platform_api.domain import geometry

    service = importlib.import_module(f"platform_api.{module_name}")
    original = geometry.crop_region
    assert service.crop_region is original
    replacement = Mock()
    monkeypatch.setattr(service, "crop_region", replacement)
    assert getattr(service, function_name).__globals__["crop_region"] is replacement
    assert geometry.crop_region is original


def test_display_title_imports_leaf_not_alias_save_service(monkeypatch):
    from platform_api import layout_names
    from platform_api.domain import layout_titles
    from platform_api.models import FloorPlan

    monkeypatch.setattr(layout_names, "layout_display_title", Mock(side_effect=AssertionError("Service used")))
    formatter = Mock(return_value="Leaf title")
    monkeypatch.setattr(layout_titles, "layout_display_title", formatter)
    plan = FloorPlan(plan_name="Original")
    assert plan.display_title == "Leaf title"
    formatter.assert_called_once_with(plan)


@pytest.mark.parametrize("module_name", [
    "platform_api.domain",
    "platform_api.domain.geometry",
    "platform_api.domain.layout_titles",
    "platform_api.policies",
    "platform_api.policies.crops",
    "platform_api.google_route_batches",
])
def test_fresh_leaf_import_needs_no_models_storage_or_django_setup(module_name):
    # A fresh process prevents pytest-django's already-loaded models from hiding
    # an accidental upward dependency. This probe never calls django.setup().
    code = textwrap.dedent("""
        import importlib
        import sys
        from types import SimpleNamespace

        sys.path.insert(0, sys.argv[1])
        module_name = sys.argv[2]
        allowed = {module_name, 'platform_api.domain', 'platform_api.policies'}
        blocked = []

        class LeafOnlyImports:
            def find_spec(self, fullname, path=None, target=None):
                if (fullname.startswith('platform_api.') and fullname not in allowed
                        or fullname == 'django.db' or fullname.startswith('django.db.')):
                    blocked.append(fullname)
                    raise AssertionError('Unexpected upward import: ' + fullname)
                return None

        sys.meta_path.insert(0, LeafOnlyImports())
        module = importlib.import_module(module_name)
        if module_name.endswith('.layout_titles'):
            plan = SimpleNamespace(display_name='', plan_name='Unit [ai segment abcdef12]',
                                   configuration_type='', plan_scope='flat', page_number=1)
            assert module.layout_display_title(plan) == 'Unit'
            assert module.validate_layout_display_name('  East  ') == 'East'
        elif module_name.endswith('.geometry'):
            regions = module.validate_regions([{'x': 0, 'y': 0, 'width': .2, 'height': .3}])
            assert isinstance(regions[0], module.UnitRegion)
        elif module_name.endswith('.crops'):
            actor = SimpleNamespace(is_active=True, is_staff=True, has_perm=lambda _: True)
            module.authorize_crops(actor, SimpleNamespace(status='active'))
            assert module.CART_LIMIT == 20
            assert module.MAX_DRAFT_BYTES == 20 * 1024 * 1024
        assert not blocked, blocked
        assert 'platform_api.models' not in sys.modules
        assert 'platform_api.artifact_storage' not in sys.modules
        assert 'django.db' not in sys.modules
        if not module_name.endswith('.geometry'):
            assert 'cv2' not in sys.modules
            assert 'numpy' not in sys.modules
            assert 'PIL' not in sys.modules
    """)
    environment = os.environ.copy()
    environment.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(Path(__file__).resolve().parents[2]), module_name],
        env=environment, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr