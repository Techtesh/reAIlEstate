"""Checkpointed Google Places matching for active projects."""

import hashlib
import json
import re
import time
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from filelock import FileLock, Timeout

from platform_api.external_sources import GoogleProjectMaps
from platform_api.models import AuditEvent, Project, ReviewTask


TERMINAL_STATES = {"matched", "ambiguous", "no_match", "skipped_existing", "insufficient_project_data"}
WORDS = re.compile(r"[a-z0-9]+")


def active_projects():
    return Project.objects.exclude(status__in={"retired", "merged"}).exclude(
        address__has_key="mergedIntoProjectId").exclude(merge_out__state="applied").order_by("created_at", "pk")


def normalized_words(value):
    return set(WORDS.findall(str(value).casefold()))


def strict_match(project, places):
    project_words = normalized_words(project.submitted_name)
    locality_words = normalized_words(project.locality)
    if not project_words or not locality_words:
        return None, "insufficient_project_data"
    matches = {}
    for place in places:
        place_words = normalized_words(place.get("name", ""))
        address_words = normalized_words(place.get("address", ""))
        if (place_words == project_words or len(project_words) >= 2 and project_words < place_words) and locality_words <= address_words:
            matches[place["placeId"]] = place
    if len(matches) == 1:
        return next(iter(matches.values())), "exact_or_project_name_with_locality"
    return None, "ambiguous" if matches else "no_match"


def report_default(name):
    return Path(settings.WORKSPACE_DATA_ROOT) / "output" / "reports" / name


def read_report(path):
    try:
        if path.stat().st_size > 20_000_000:
            raise ValueError
        report = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError
        return report
    except (OSError, ValueError):
        raise CommandError("Cannot read checkpoint: expected a JSON object smaller than 20 MB.") from None


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def existing_reference(project):
    return ReviewTask.objects.filter(project=project, task_type="external_sources",
        resolution__googlePlaceId__isnull=False).order_by("-created_at", "-pk").first()


def create_reference(project, place_id, evidence_method, **metadata):
    if not isinstance(place_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", place_id):
        raise ValueError("Invalid Google place reference.")
    with transaction.atomic():
        locked = Project.objects.select_for_update().get(pk=project.pk)
        if not active_projects().filter(pk=locked.pk).exists() or locked.updated_at != project.updated_at:
            raise ValueError("Project changed. Review again before submitting.")
        existing = existing_reference(locked)
        if existing:
            return existing
        task = ReviewTask.objects.create(project=locked, task_type="external_sources", priority=3, resolution={
            "googlePlaceId": place_id, "evidenceMethod": evidence_method, "reviewState": "unreviewed", **metadata})
        AuditEvent.objects.create(action="project.external_sources_google_submitted", entity_type="ReviewTask",
            entity_id=task.pk, correlation_id=uuid4(), metadata={"projectId": str(project.pk)})
        return task


class Command(BaseCommand):
    help = "Match active projects to Google Places and submit strict unique matches for review."

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true")
        parser.add_argument("--resume", action="store_true")
        parser.add_argument("--retry-failed", action="store_true")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--delay-seconds", type=float, default=0.25)
        parser.add_argument("--report", type=Path, default=report_default("google-project-places.json"))

    def handle(self, *args, **options):
        if not options["execute"]:
            self.stdout.write("Preview only: add --execute to call Google Places and create unreviewed references.")
            return
        if not getattr(settings, "GOOGLE_MAPS_API_KEY", ""):
            raise CommandError("GOOGLE_MAPS_API_KEY is not configured in this process.")
        if options["limit"] is not None and not 1 <= options["limit"] <= 10000:
            raise CommandError("--limit must be between 1 and 10000.")
        if not 0.1 <= options["delay_seconds"] <= 10:
            raise CommandError("--delay-seconds must be between 0.1 and 10.")
        path = options["report"].expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with FileLock(str(path.with_suffix(".lock")), timeout=0):
                self._run(path, options)
        except Timeout:
            raise CommandError("Another Google project refresh is already running.") from None

    def _run(self, path, options):
        if path.with_suffix(".stop").exists():
            raise CommandError("Remove the report's .stop marker before starting.")
        old = read_report(path) if options["resume"] else {}
        previous = {row["projectId"]: row for row in old.get("projects", [])}
        report = {"startedAt": old.get("startedAt", timezone.now().isoformat()), "state": "running",
            "provider": "google_places_new", "routesRequested": False, "projects": list(previous.values())}
        attempted = 0
        maps = GoogleProjectMaps()
        for project in active_projects():
            project_id = str(project.pk)
            old_row = previous.get(project_id, {})
            query = f"{project.submitted_name}, {project.locality}, Maharashtra, India"
            digest = hashlib.sha256(query.encode()).hexdigest()
            unchanged = old_row.get("queryDigest") == digest
            if unchanged and (old_row.get("status") in TERMINAL_STATES or
                    old_row.get("status") == "failed" and not options["retry_failed"]):
                continue
            if path.with_suffix(".stop").exists() or options["limit"] is not None and attempted >= options["limit"]:
                report["state"] = "paused"
                break
            row = {"projectId": project_id, "queryDigest": digest}
            if existing_reference(project):
                row["status"] = "skipped_existing"
            elif not normalized_words(project.submitted_name) or not normalized_words(project.locality):
                row["status"] = "insufficient_project_data"
            else:
                attempted += 1
                try:
                    places = maps.search(query)
                    selected, reason = strict_match(project, places)
                    row.update(status=reason, candidateCount=len(places))
                    if selected:
                        task = create_reference(project, selected["placeId"], "google_places_strict_match", matchRule=reason)
                        row.update(status="matched", reviewTaskId=str(task.pk), selectedPlaceId=task.resolution["googlePlaceId"])
                except ValueError as exc:
                    row.update(status="failed", error=str(exc)[:300])
                time.sleep(options["delay_seconds"])
            report["projects"] = [item for item in report["projects"] if item["projectId"] != project_id] + [row]
            report["updatedAt"] = timezone.now().isoformat()
            write_report(path, report)
        else:
            report["state"] = "completed"
        report["counts"] = {state: sum(row["status"] == state for row in report["projects"])
            for state in {row["status"] for row in report["projects"]}}
        report["updatedAt"] = timezone.now().isoformat()
        write_report(path, report)
        self.stdout.write(f"Google project refresh {report['state']}: {report['counts']}")