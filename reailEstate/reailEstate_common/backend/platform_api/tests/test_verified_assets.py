import hashlib
import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from platform_api.infrastructure.verified_assets import read_verified_asset


class RecordingStream(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


def asset_for(data):
    # No model, authorization or database access is needed by this primitive.
    return SimpleNamespace(object_key="test/source.png", byte_length=len(data),
                           sha256=hashlib.sha256(data).hexdigest())


@pytest.mark.parametrize("max_bytes", [None, 8, 100])
def test_verified_read_is_bounded_and_closes_stream(max_bytes):
    data = b"evidence"
    stream = RecordingStream(data)
    backend = Mock()
    backend.open.return_value = stream
    assert read_verified_asset(asset_for(data), backend, max_bytes=max_bytes) == data
    backend.open.assert_called_once_with("test/source.png", "rb")
    assert stream.read_sizes == [len(data) + 1]
    assert stream.closed


@pytest.mark.parametrize("stored", [b"evidenc", b"evidence-extra", b"Evidence"])
def test_verified_read_rejects_short_long_and_same_length_corruption(stored):
    expected = b"evidence"
    stream = RecordingStream(stored)
    backend = Mock()
    backend.open.return_value = stream
    with pytest.raises(ValueError, match="^IMAGE_INTEGRITY_MISMATCH$"):
        read_verified_asset(asset_for(expected), backend)
    assert stream.read_sizes == [len(expected) + 1]
    assert stream.closed


def test_size_ceiling_rejects_before_open_without_truncating():
    backend = Mock()
    with pytest.raises(ValueError, match="^IMAGE_INTEGRITY_MISMATCH$"):
        read_verified_asset(asset_for(b"evidence"), backend, max_bytes=7)
    backend.open.assert_not_called()


@pytest.mark.parametrize("length", [-2, -1, True, "8", None])
def test_invalid_length_cannot_trigger_unbounded_read(length):
    asset = asset_for(b"evidence")
    asset.byte_length = length
    backend = Mock()
    with pytest.raises(ValueError, match="^IMAGE_INTEGRITY_MISMATCH$"):
        read_verified_asset(asset, backend)
    backend.open.assert_not_called()


@pytest.mark.parametrize("max_bytes", [-1, True, "8"])
def test_invalid_size_ceiling_rejects_before_open(max_bytes):
    backend = Mock()
    with pytest.raises(ValueError, match="max_bytes"):
        read_verified_asset(asset_for(b"evidence"), backend, max_bytes=max_bytes)
    backend.open.assert_not_called()


def test_default_storage_and_missing_file_behavior(monkeypatch):
    from platform_api.artifact_storage import storage
    open_stream = Mock(return_value=io.BytesIO(b"evidence"))
    monkeypatch.setattr(storage, "open", open_stream)
    assert read_verified_asset(asset_for(b"evidence")) == b"evidence"
    open_stream.side_effect = FileNotFoundError("missing")
    with pytest.raises(FileNotFoundError):
        read_verified_asset(asset_for(b"evidence"))


def test_segmentation_source_loader_reuses_primitive_without_changing_gates(monkeypatch):
    from platform_api import floor_plan_segmentation_service as service
    asset = SimpleNamespace(asset_type="page_render", status="ready")
    plan = SimpleNamespace(asset=asset, document=SimpleNamespace(scan_status="clean"))
    manager = Mock()
    manager.select_related.return_value.get.return_value = plan
    monkeypatch.setattr(service, "FloorPlan", SimpleNamespace(objects=manager))
    reader = Mock(return_value=b"verified")
    monkeypatch.setattr(service, "read_verified_asset", reader)
    assert service._load_source("plan-id") == (plan, b"verified")
    reader.assert_called_once_with(asset, storage_backend=service.storage)
    reader.reset_mock()
    plan.document.scan_status = "pending"
    with pytest.raises(ValueError, match="^SOURCE_ASSET_NOT_READY$"):
        service._load_source("plan-id")
    reader.assert_not_called()