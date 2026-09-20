"""Verify a dumpdata transfer without printing user data, text evidence or credentials."""

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from django.apps import apps
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, models
from django.utils.dateparse import parse_datetime


class Command(BaseCommand):
    help = "Compare all fixture field values/relations to the target database, saving only digests and counts."

    def add_arguments(self, parser):
        parser.add_argument("fixture", type=Path)
        parser.add_argument("--report", type=Path, required=True)

    def handle(self, *args, **options):
        expected = defaultdict(list)
        for item in json.loads(options["fixture"].read_text(encoding="utf-8")):
            expected[item["model"]].append(item)
        checks = []
        for label, items in expected.items():
            model = apps.get_model(label)
            actual = json.loads(serializers.serialize("json", model._base_manager.all(),
                                use_natural_foreign_keys=True, use_natural_primary_keys=True))
            # M2M ordering is not part of relational semantics; JSON-field array ordering is.
            def fingerprints(records):
                result = []
                for record in records:
                    for column in model._meta.fields:
                        value = record["fields"].get(column.name)
                        if value and isinstance(column, models.DateTimeField):
                            record["fields"][column.name] = parse_datetime(value).isoformat(timespec="microseconds")
                    for relation in model._meta.many_to_many:
                        value = record["fields"].get(relation.name)
                        if isinstance(value, list):
                            record["fields"][relation.name] = sorted(value, key=lambda item: json.dumps(item, sort_keys=True))
                    result.append(hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=True).encode()).hexdigest())
                return Counter(result)
            matched = fingerprints(items) == fingerprints(actual)
            checks.append({"model": label, "sourceCount": len(items), "targetCount": len(actual), "fieldsAndRelationsMatch": matched})
        report = {"databaseVendor": connection.vendor, "matched": all(item["fieldsAndRelationsMatch"] for item in checks), "models": checks}
        options["report"].parent.mkdir(parents=True, exist_ok=True)
        options["report"].write_text(json.dumps(report, indent=2), encoding="utf-8")
        if not report["matched"]:
            raise CommandError("Transfer mismatch: " + ", ".join(item["model"] for item in checks if not item["fieldsAndRelationsMatch"]))
        self.stdout.write(f"Verified {sum(item['sourceCount'] for item in checks)} records across {len(checks)} populated models: fields and relations match.")