"""Regression cases for transaction scope and process ownership (PostgreSQL cases are opt-in)."""

import threading
import uuid
from unittest.mock import patch

import pytest
from django.db import connection, connections
from django.contrib.sessions.backends.db import SessionStore

from platform_api.ingestion_runtime import work_lock, WorkAlreadyClaimed
from platform_api.management.commands.refresh_sample_corpus import Command
from platform_api.workers import process_document


@pytest.mark.django_db(transaction=True)
def test_document_wrapper_does_not_open_transaction(tmp_path, settings):
    settings.WORKSPACE_DATA_ROOT = tmp_path
    result = type("Result", (), {})()
    result.status = "needs_review"
    result.error_category = ""
    from unittest.mock import Mock
    result.ingestion_runs = Mock()
    result.ingestion_runs.order_by.return_value.first.return_value = None

    def extract(*args):
        assert not connection.in_atomic_block
        return result

    with patch("platform_api.workers._process_document", side_effect=extract):
        assert process_document(uuid.uuid4()) is result


def test_duplicate_alias_preserves_source_path():
    row = {"path": "copy.pdf", "state": "queued", "duplicateOf": "original.pdf", "sha256": "abc"}
    original = {"path": "original.pdf", "state": "completed", "documentId": "one", "sha256": "abc"}
    Command._aliases(original, {"original.pdf": [row]})
    assert row["path"] == "copy.pdf"
    assert row["state"] == "completed"
    assert row["documentId"] == "one"


@pytest.mark.django_db(transaction=True)
def test_postgres_ownership_does_not_block_sessions():
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL session locks require PostgreSQL")
    identifier = uuid.uuid4()
    outcomes = []

    def contender():
        try:
            with pytest.raises(WorkAlreadyClaimed):
                with work_lock("document", identifier):
                    pass
            session = SessionStore()
            session["probe"] = True
            session.save()
            session.delete()
            outcomes.append("session_written")
        finally:
            connections.close_all()

    with work_lock("document", identifier):
        assert not connection.in_atomic_block
        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert outcomes == ["session_written"]