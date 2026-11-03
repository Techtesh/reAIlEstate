"""PostgreSQL-only startup and backup boundaries; no live providers or source data."""

import runpy
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from django.core.management.base import CommandError

from platform_api.management.commands import rebuild_script_outputs as maintenance


@pytest.mark.parametrize("configured", [False, True])
def test_postgresql_is_required_without_local_credentials(monkeypatch, configured):
    monkeypatch.delenv("DATABASE_BACKEND", raising=False)
    monkeypatch.setenv("PROJECT_METRO_CATALOGUE_FILE", "")
    for name, value in {"PGHOST": "database.example", "PGPORT": "5432", "PGDATABASE": "workspace",
                        "PGUSER": "service", "PGPASSWORD": "test-secret", "PGSSLMODE": "verify-full"}.items():
        if configured:
            monkeypatch.setenv(name, value)
        else:
            monkeypatch.delenv(name, raising=False)
    settings_path = Path(__file__).resolve().parents[2] / "config" / "settings.py"
    with patch.object(Path, "exists", return_value=False), patch.object(sys, "path", sys.path.copy()):
        database = runpy.run_path(str(settings_path), run_name="config._postgres_configuration_test")["DATABASES"]["default"]
    assert database["ENGINE"] == "django.db.backends.postgresql"
    assert database["NAME"] == ("workspace" if configured else "realestate")
    if configured:
        assert database["HOST"] == "database.example"
        assert database["PASSWORD"] == "test-secret"
        assert database["OPTIONS"]["sslmode"] == "verify-full"


@pytest.mark.parametrize("backend", ["sqlite", "mysql", ""])
def test_stale_backend_override_fails_before_database_access(monkeypatch, backend):
    monkeypatch.setenv("DATABASE_BACKEND", backend)
    settings_path = Path(__file__).resolve().parents[2] / "config" / "settings.py"
    with patch.object(sys, "path", sys.path.copy()), pytest.raises(ValueError, match="PostgreSQL is the only supported database"):
        runpy.run_path(str(settings_path), run_name="config._postgres_configuration_test")


@pytest.mark.parametrize("stage", ["dump", "catalogue"])
@pytest.mark.parametrize("failure", [FileNotFoundError("pg_dump"), subprocess.CalledProcessError(1, "pg_dump")])
def test_failed_postgres_backup_stops_before_media_or_cleanup(tmp_path, monkeypatch, failure, stage):
    run = Mock(side_effect=[failure] if stage == "dump" else [None, failure])
    monkeypatch.setattr(maintenance.subprocess, "run", run)
    command = maintenance.Command()
    save = Mock()
    monkeypatch.setattr(command, "_save", save)
    with pytest.raises(CommandError, match="Nothing was cleaned"):
        command._backup(tmp_path, {})
    assert list(tmp_path.iterdir()) == []
    save.assert_not_called()
    assert run.call_count == (1 if stage == "dump" else 2)
    dump = run.call_args_list[0]
    arguments = dump.args[0]
    assert arguments[0] == "pg_dump" and "--format=custom" in arguments
    assert "--file" in arguments and str(tmp_path / "database.dump") in arguments
    assert dump.kwargs["check"] is True
    assert "PGPASSWORD" in dump.kwargs["env"]
    assert not any(argument.startswith("--password=") for argument in arguments)

    if stage == "catalogue":
        assert run.call_args.args[0] == ["pg_restore", "--list", str(tmp_path / "database.dump")]
