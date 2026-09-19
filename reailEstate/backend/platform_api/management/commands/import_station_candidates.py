import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from platform_api.external_sources import coordinates
from platform_api.models import StationCandidate


class Command(BaseCommand):
    help = "Import unverified station candidates without overwriting existing database edits."

    def add_arguments(self, parser):
        parser.add_argument("csv_file")

    def handle(self, *args, **options):
        try:
            path = Path(options["csv_file"])
            if path.stat().st_size > 1_000_000:
                raise ValueError("Candidate file exceeds 1 MB.")
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            if len(rows) > 1000:
                raise ValueError("At most 1000 rows are supported.")
        except (OSError, UnicodeError, csv.Error, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        grouped = {}
        for sequence, row in enumerate(rows):
            name, line, kind = row.get("name", "").strip(), row.get("line", "").strip(), row.get("kind", "metro").strip()
            if not name or len(name) > 255 or len(line) > 128 or kind not in StationCandidate.Kind.values:
                raise CommandError(f"Row {sequence + 2}: a valid name, line and kind are required.")
            entry = grouped.setdefault((kind, name.casefold(), line.casefold()), {
                "name": name, "line": line, "kind": kind, "sequence": sequence, "options": [], "invalid": False})
            try:
                location = coordinates({"latitude": float(row["latitude"]), "longitude": float(row["longitude"])})
            except (KeyError, TypeError, ValueError):
                entry["invalid"] = True
                continue
            if location not in entry["options"]:
                entry["options"].append(location)
        created = queued = 0
        with transaction.atomic():
            for entry in grouped.values():
                needs_review = entry["invalid"] or len(entry["options"]) != 1
                location = entry["options"][0] if not needs_review else {}
                issue = ("Choose the correct coordinate pair from coordinate_options, then clear needs_review."
                         if len(entry["options"]) > 1 else "Supply valid latitude and longitude, then clear needs_review.")
                existing = StationCandidate.objects.filter(kind=entry["kind"], name__iexact=entry["name"], line__iexact=entry["line"]).exists()
                if existing:
                    continue
                StationCandidate.objects.create(name=entry["name"], kind=entry["kind"], line=entry["line"],
                    sequence=entry["sequence"], latitude=location.get("latitude"), longitude=location.get("longitude"),
                    needs_review=needs_review, issue=issue if needs_review else "", coordinate_options=entry["options"])
                created += 1
                queued += int(needs_review)
        self.stdout.write(f"Imported {created} stations; {queued} need review. Existing database entries were preserved.")