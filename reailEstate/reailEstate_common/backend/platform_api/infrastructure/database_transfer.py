"""Canonical fixture fingerprints for database transfer verification."""

import hashlib
import json
from collections import Counter, defaultdict

from django.core import serializers
from django.db import models
from django.utils.dateparse import parse_datetime


def record_fingerprints(model, records, *, normalize_datetimes=False):
    """Ignore M2M ordering, preserve JSON array ordering and duplicate counts."""
    result = Counter()
    fields = defaultdict(Counter)
    for original in records:
        record = {**original, "fields": dict(original["fields"])}
        if normalize_datetimes:
            for column in model._meta.fields:
                value = record["fields"].get(column.name)
                if value and isinstance(column, models.DateTimeField):
                    record["fields"][column.name] = parse_datetime(value).isoformat(timespec="microseconds")
        for relation in model._meta.many_to_many:
            value = record["fields"].get(relation.name)
            if isinstance(value, list):
                record["fields"][relation.name] = sorted(value, key=lambda item: json.dumps(item, sort_keys=True))
        result[_digest(record)] += 1
        for name, value in record["fields"].items():
            fields[name][_digest(value)] += 1
    return result, fields


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def fingerprints(model, database):
    records = serializers.serialize(
        "json", model._base_manager.using(database).all(),
        use_natural_foreign_keys=True, use_natural_primary_keys=True,
    )
    return record_fingerprints(model, json.loads(records))
