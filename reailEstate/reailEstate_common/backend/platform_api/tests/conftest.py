import pytest


@pytest.fixture(autouse=True)
def private_artifacts(settings, tmp_path):
    """Never write test uploads or reports into real workspace output directories."""
    settings.WORKSPACE_DATA_ROOT = tmp_path / "workspace"
    settings.OFFICE_CONVERTER_ENABLED = False
    settings.PROJECT_ENRICHMENT_BACKEND = ""
    settings.PROJECT_PUBLIC_PREVIEW_ENABLED = False
    settings.CACHES = {"default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": str(tmp_path),
    }}