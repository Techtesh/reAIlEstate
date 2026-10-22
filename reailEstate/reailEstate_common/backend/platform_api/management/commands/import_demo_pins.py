"""Import the controlled DemoPins browse dataset from scraper JSON files."""

import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from platform_api.feature_flags.registry import REGISTRY
from platform_api.models import FeatureFlag, Organization, Project


DEMO_ORGANIZATION = "DemoPins"
DEMO_FLAG = "showDemoPinsOnBrowse"
RERA_ID = re.compile(r"P517[0-9]{8}\Z")
MAX_FILES = 5_000
MAX_FILE_BYTES = 100_000


def coordinate(value, *, lower, upper, field, filename):
    if isinstance(value, bool):
        raise CommandError(f"{filename}: {field} must be a number.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise CommandError(f"{filename}: {field} must be a number.") from None
    if not result.is_finite() or not lower <= result <= upper:
        raise CommandError(f"{filename}: {field} is outside its valid range.")
    return result.quantize(Decimal("0.000001"))


def string(value, *, field, filename, maximum, required=True):
    result = value.strip() if isinstance(value, str) else ""
    if required and not result:
        raise CommandError(f"{filename}: {field} is required.")
    if len(result) > maximum:
        raise CommandError(f"{filename}: {field} is too long.")
    return result


def source_url(value, *, field, filename):
    result = string(value, field=field, filename=filename, maximum=2_000, required=False)
    if result and not result.startswith("https://"):
        raise CommandError(f"{filename}: {field} must be an HTTPS URL.")
    return result


class Command(BaseCommand):
    help = "Import validated scraper records as unverified DemoPins projects without publishing facts."

    def add_arguments(self, parser):
        parser.add_argument("source", type=Path, help="Folder containing detail3 JSON records.")
        parser.add_argument("--dry-run", action="store_true", help="Validate and report without writing.")

    def handle(self, *args, **options):
        source = options["source"].resolve()
        try:
            files = sorted(source.glob("*.json"))
        except OSError as exc:
            raise CommandError("The source folder cannot be read.") from exc
        if not source.is_dir() or not files:
            raise CommandError("Choose a folder containing scraper JSON records.")
        if len(files) > MAX_FILES:
            raise CommandError(f"At most {MAX_FILES} JSON records are supported.")

        records, by_rera = [], {}
        for path in files:
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    raise CommandError(f"{path.name}: file exceeds {MAX_FILE_BYTES} bytes.")
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise CommandError(f"{path.name}: invalid JSON input.") from exc
            if not isinstance(payload, dict):
                raise CommandError(f"{path.name}: the record must be a JSON object.")
            rera_id = string(payload.get("rera_id_detail"), field="rera_id_detail", filename=path.name, maximum=64).upper()
            if not RERA_ID.fullmatch(rera_id):
                raise CommandError(f"{path.name}: rera_id_detail is invalid.")
            if rera_id in by_rera:
                raise CommandError(f"{path.name}: duplicate rera_id_detail in source ({rera_id}).")
            locality = ""
            project_ld = payload.get("project_ld")
            if isinstance(project_ld, dict) and isinstance(project_ld.get("address"), dict):
                locality = string(project_ld["address"].get("addressLocality"), field="addressLocality", filename=path.name, maximum=128, required=False)
            record = {
                "maha_rera_id": rera_id,
                "submitted_name": string(payload.get("detail_name"), field="detail_name", filename=path.name, maximum=255),
                "locality": locality,
                "latitude": coordinate(payload.get("lat"), lower=Decimal("-90"), upper=Decimal("90"), field="lat", filename=path.name),
                "longitude": coordinate(payload.get("lng"), lower=Decimal("-180"), upper=Decimal("180"), field="lng", filename=path.name),
                "address": {
                    "demoPin": True,
                    "displayAddress": string(payload.get("address"), field="address", filename=path.name, maximum=2_000, required=False),
                    "pincode": string(str(payload.get("pincode", "")), field="pincode", filename=path.name, maximum=32, required=False),
                    "source": {
                        "kind": "propp_detail3",
                        "fileName": path.name,
                        "mapsSource": string(payload.get("maps_source"), field="maps_source", filename=path.name, maximum=128, required=False),
                        "reraUrl": source_url(payload.get("rera_link"), field="rera_link", filename=path.name),
                        "mapsUrl": source_url(payload.get("maps_link"), field="maps_link", filename=path.name),
                        "importedAt": datetime.now(timezone.utc).isoformat(),
                    },
                },
            }
            by_rera[rera_id] = record
            records.append(record)

        existing = {project.maha_rera_id: project for project in Project.objects.filter(maha_rera_id__in=by_rera).select_related("organization")}
        conflicts = [rera for rera, project in existing.items() if project.organization.legal_name != DEMO_ORGANIZATION]
        if conflicts:
            raise CommandError(f"{len(conflicts)} MahaRERA ID(s) already belong to a non-demo organization; import aborted.")
        new_records = [record for record in records if record["maha_rera_id"] not in existing]
        if options["dry_run"]:
            self.stdout.write(f"Validated {len(records)} records; would create {len(new_records)} DemoPins projects and preserve {len(existing)} existing demo projects.")
            return

        with transaction.atomic():
            organization = Organization.objects.filter(legal_name=DEMO_ORGANIZATION).order_by("created_at", "id").first()
            if organization is None:
                organization = Organization.objects.create(legal_name=DEMO_ORGANIZATION)
            FeatureFlag.objects.get_or_create(
                key=DEMO_FLAG,
                environment=settings.ENVIRONMENT,
                defaults={"enabled": REGISTRY[DEMO_FLAG].default_enabled, "targeting_rules": {}, "version": 1},
            )
            Project.objects.bulk_create([
                Project(organization=organization, status=Project.Status.NEEDS_REVIEW,
                        verification_status=Project.VerificationStatus.UNVERIFIED, **record)
                for record in new_records
            ], batch_size=250)
        self.stdout.write(f"DemoPins ready: created {len(new_records)} projects; preserved {len(existing)} existing demo projects; {DEMO_FLAG}=true by default.")
