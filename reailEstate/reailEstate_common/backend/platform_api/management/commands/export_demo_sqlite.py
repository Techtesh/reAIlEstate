"""Convert an already sanitized, isolated PostgreSQL snapshot to a new SQLite file."""

import hashlib
import json
import re
import tempfile
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import serializers
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, connections


EXCLUDED_MODELS = {"contenttypes.contenttype", "auth.permission"}


def fingerprints(model, database):
    records = serializers.serialize(
        "json", model._base_manager.using(database).all(),
        use_natural_foreign_keys=True, use_natural_primary_keys=True,
    )
    result = Counter()
    fields = defaultdict(Counter)
    for record in json.loads(records):
        for relation in model._meta.many_to_many:
            value = record["fields"].get(relation.name)
            if isinstance(value, list):
                record["fields"][relation.name] = sorted(value, key=lambda item: json.dumps(item, sort_keys=True))
        result[hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=True).encode()).hexdigest()] += 1
        for name, value in record["fields"].items():
            fields[name][hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()] += 1
    return result, fields


class Command(BaseCommand):
    help = "Export a sanitized demo_export database to a NEW SQLite file; never converts the live database."

    def add_arguments(self, parser):
        parser.add_argument("output", type=Path)

    def handle(self, *args, **options):
        if connection.vendor != "postgresql" or not re.fullmatch(
            r"demo_(?:export|verify)_[a-f0-9]{32}", str(connection.settings_dict["NAME"])
        ):
            raise CommandError("Use an isolated, sanitized demo_export/demo_verify PostgreSQL database.")
        users = get_user_model().objects
        if users.count() != 1 or not users.filter(
            username="demo-evidence-placeholder", password="!", email="", first_name="", last_name="",
            is_active=False, is_staff=False, is_superuser=False, last_login__isnull=True,
        ).exists():
            raise CommandError("Source still contains original accounts or a usable login.")
        empty_models = (
            "sessions.Session", "admin.LogEntry", "platform_api.OrganizationMembership",
            "platform_api.IdempotencyRecord", "platform_api.RequirementProfile",
            "platform_api.Shortlist", "platform_api.ComparisonShare",
            "platform_api.NotificationSubscription", "platform_api.OutboxMessage",
        )
        if any(apps.get_model(label).objects.exists() for label in empty_models):
            raise CommandError("Source still contains personal records or operational queues.")
        user = users.get()
        if user.groups.exists() or user.user_permissions.exists():
            raise CommandError("Placeholder must not have assigned access.")

        output = options["output"].resolve()
        if output.exists() or output.suffix != ".sqlite3" or not output.parent.is_dir():
            raise CommandError("Choose a new .sqlite3 file in an existing directory; overwrites are refused.")
        alias = "demo_sqlite_export"
        if alias in connections.databases:
            raise CommandError("Export database alias is already configured.")
        database_settings = deepcopy(connection.settings_dict)
        database_settings.update(
            ENGINE="django.db.backends.sqlite3", NAME=str(output), OPTIONS={},
            USER="", PASSWORD="", HOST="", PORT="", CONN_MAX_AGE=0,
        )
        connections.databases[alias] = database_settings
        models = [model for model in apps.get_models() if model._meta.label_lower not in EXCLUDED_MODELS]
        try:
            # Keep the transient fixture on the local machine, not on the transfer drive.
            with tempfile.TemporaryDirectory(prefix="demo-sqlite-", dir=settings.BASE_DIR / ".local") as folder:
                fixture = Path(folder) / "sanitized.json"
                with fixture.open("w", encoding="utf-8") as stream:
                    call_command(
                        "dumpdata", database="default", exclude=list(EXCLUDED_MODELS),
                        use_natural_foreign_keys=True, use_natural_primary_keys=True,
                        use_base_manager=True, stdout=stream, verbosity=0,
                    )
                output.touch(exist_ok=False)
                call_command("migrate", database=alias, interactive=False, verbosity=0)
                call_command("loaddata", str(fixture), database=alias, verbosity=0)
                checks = []
                for model in models:
                    source, source_fields = fingerprints(model, "default")
                    target, target_fields = fingerprints(model, alias)
                    checks.append({
                        "model": model._meta.label_lower, "sourceCount": sum(source.values()),
                        "targetCount": sum(target.values()), "fieldsAndRelationsMatch": source == target,
                        "mismatchedFields": sorted(
                            name for name in source_fields.keys() | target_fields.keys()
                            if source_fields[name] != target_fields[name]
                        ),
                    })
                with connections[alias].cursor() as cursor:
                    cursor.execute("PRAGMA integrity_check")
                    integrity = cursor.fetchall() == [("ok",)]
                    cursor.execute("PRAGMA foreign_key_check")
                    foreign_keys = not cursor.fetchall()
                verified = integrity and foreign_keys and all(check["fieldsAndRelationsMatch"] for check in checks)
                report = {
                    "verified": verified, "integrityCheck": integrity, "foreignKeysValid": foreign_keys,
                    "models": checks, "mediaIncluded": False, "originalAccountsIncluded": False,
                }
                output.with_suffix(".verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
                if not verified:
                    mismatches = "; ".join(
                        f"{check['model']}: {','.join(check['mismatchedFields'])}"
                        for check in checks if not check["fieldsAndRelationsMatch"]
                    )
                    raise CommandError(
                        f"SQLite verification failed (integrity={integrity}, foreign keys={foreign_keys}, "
                        f"mismatched models: {mismatches}); do not use the incomplete output."
                    )
                self.stdout.write(
                    f"SQLite verified: {sum(check['targetCount'] for check in checks)} records "
                    f"across {len(checks)} models; fields, relations, integrity and foreign keys match."
                )
        finally:
            connections[alias].close()
            del connections[alias]
            del connections.databases[alias]
