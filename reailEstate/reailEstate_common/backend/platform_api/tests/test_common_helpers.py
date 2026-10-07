"""Regression contracts for shared checkpoint and transfer helpers."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.db import models

from platform_api.infrastructure.database_transfer import record_fingerprints
from realestate_common.reports import write_json_report


def test_failed_checkpoint_serialization_preserves_previous_report(tmp_path):
    path = tmp_path / "checkpoint.json"
    write_json_report(path, {"progress": 1})
    original = path.read_bytes()
    with pytest.raises(ValueError):
        write_json_report(path, {"progress": float("nan")}, allow_nan=False)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_failed_checkpoint_replace_preserves_previous_report_and_cleans_temp(tmp_path, monkeypatch):
    path = tmp_path / "checkpoint.json"
    write_json_report(path, {"progress": 1})
    original = path.read_bytes()
    def locked(*args):
        raise PermissionError("Checkpoint held by reader")
    monkeypatch.setattr(Path, "replace", locked)
    with pytest.raises(PermissionError):
        write_json_report(path, {"progress": 2})
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_checkpoint_retries_transient_windows_sharing_conflict(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "checkpoint.json"
    replace = Path.replace
    attempts = []
    def briefly_locked(source, target):
        attempts.append(target)
        if len(attempts) == 1:
            raise PermissionError("Reader still holds checkpoint")
        return replace(source, target)
    monkeypatch.setattr(Path, "replace", briefly_locked)
    monkeypatch.setattr("realestate_common.reports.time.sleep", lambda _: None)
    write_json_report(path, {"label": "é"}, ensure_ascii=False, newline=True, replace_attempts=2)
    assert 'é' in path.read_text(encoding="utf-8")
    assert path.read_bytes().endswith(b"\n")
    assert len(attempts) == 2
    assert list(path.parent.iterdir()) == [path]


def fixture_model():
    timestamp = models.DateTimeField()
    timestamp.set_attributes_from_name("created_at")
    return SimpleNamespace(_meta=SimpleNamespace(
        fields=[timestamp], many_to_many=[SimpleNamespace(name="members")],
    ))


def fixture_record():
    return {"model": "test.project", "pk": 1, "fields": {
        "members": [2, 1], "ordered_json": [2, 1], "created_at": "2026-01-01T12:00:00Z",
    }}


def test_transfer_fingerprints_ignore_relation_order_without_mutating_records():
    model = fixture_model()
    original = fixture_record()
    copy = deepcopy(original)
    reordered = deepcopy(original)
    reordered["fields"]["members"].reverse()
    expected = record_fingerprints(model, [original])
    assert record_fingerprints(model, [reordered]) == expected
    assert original == copy
    reordered["fields"]["ordered_json"].reverse()
    actual = record_fingerprints(model, [reordered])
    assert actual[0] != expected[0]
    assert actual[1]["ordered_json"] != expected[1]["ordered_json"]


def test_transfer_fingerprints_preserve_duplicate_count_and_normalize_timestamps():
    model = fixture_model()
    record = fixture_record()
    equivalent = deepcopy(record)
    equivalent["fields"]["created_at"] = "2026-01-01T12:00:00.000000+00:00"
    expected = record_fingerprints(model, [record], normalize_datetimes=True)
    assert record_fingerprints(model, [equivalent], normalize_datetimes=True) == expected
    assert record_fingerprints(model, [equivalent]) != record_fingerprints(model, [record])
    duplicates = record_fingerprints(model, [record, record], normalize_datetimes=True)
    assert duplicates[0] != expected[0]
    assert sum(duplicates[0].values()) == 2
