"""Explicitly review locations and persist station routes in bounded batches."""

import time
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from filelock import FileLock, Timeout

from platform_api.external_sources_admin import enrich_google_reference
from platform_api.models import ReviewTask
from .refresh_google_project_places import active_projects, read_report, report_default, write_report

def google_references():
    references = {}
    for task in ReviewTask.objects.filter(task_type="external_sources").order_by("created_at", "pk"):
        if isinstance(task.resolution, dict) and task.resolution.get("googlePlaceId"):
            references[task.project_id] = task
    return references


def has_available_route(row):
    return any(station.get(mode, {}).get("status") == "available"
        for group in row.get("routes", {}).get("groups", []) for station in group.get("stations", [])
        for mode in ("walking", "driving"))


class Command(BaseCommand):
    help = "Accept reviewed Google references and save station routes (maximum five provider projects per run)."

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true", help="Confirm review and apply Google locations.")
        parser.add_argument("--resume", action="store_true")
        parser.add_argument("--retry-failed", action="store_true")
        parser.add_argument("--retry-generation", default="")
        parser.add_argument("--batch-size", type=int, default=5)
        parser.add_argument("--radius", type=int, choices=(5000, 10000, 20000), default=10000)
        parser.add_argument("--delay-seconds", type=float, default=0.25)
        parser.add_argument("--reviewer")
        parser.add_argument("--report", type=Path, default=report_default("google-project-routes.json"))

    def handle(self, *args, **options):
        if not options["execute"]:
            self.stdout.write("Preview only: --execute confirms review, calls Google APIs and applies project locations.")
            return
        if not getattr(settings, "GOOGLE_MAPS_API_KEY", ""):
            raise CommandError("GOOGLE_MAPS_API_KEY is not configured in this process.")
        if not 1 <= options["batch_size"] <= 5:
            raise CommandError("--batch-size must be between 1 and 5.")
        if not 0.1 <= options["delay_seconds"] <= 10:
            raise CommandError("--delay-seconds must be between 0.1 and 10.")
        if options["retry_failed"] and not options["retry_generation"]:
            raise CommandError("--retry-failed requires --retry-generation.")
        reviewer = self._reviewer(options.get("reviewer"))
        path = options["report"].expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with FileLock(str(path.with_suffix(".lock")), timeout=0):
                self._run(path, reviewer, options)
        except Timeout:
            raise CommandError("Another route enrichment is already using this report.") from None

    def _run(self, path, reviewer, options):
        old = read_report(path) if options["resume"] else {}
        previous = {row["projectId"]: row for row in old.get("projects", [])}
        references = google_references()
        projects = list(active_projects())
        project_ids = {str(project.pk) for project in projects}
        report = {"state": "running", "radiusMeters": options["radius"],
            "projects": [row for key, row in previous.items() if key in project_ids]}
        attempted = 0
        paused = False
        for project in projects:
            project_id = str(project.pk)
            task = references.get(project.pk)
            old_row = previous.get(project_id, {})
            same_reference = (task and old_row.get("reviewTaskId") == str(task.pk)
                and old_row.get("googlePlaceId") == task.resolution["googlePlaceId"]
                and old_row.get("radiusMeters") == options["radius"])
            retry = options["retry_failed"] and old_row.get("retryGeneration") != options["retry_generation"]
            if same_reference and old_row.get("status") in {"enriched", "failed"} and not (
                    retry and (old_row["status"] == "failed" or not has_available_route(old_row))):
                continue
            row = {"projectId": project_id}
            if not task:
                row["status"] = "skipped_unavailable"
            elif attempted >= options["batch_size"]:
                row = old_row or {"projectId": project_id, "status": "pending"}
                paused = True
            else:
                attempted += 1
                row.update(reviewTaskId=str(task.pk), googlePlaceId=task.resolution["googlePlaceId"], radiusMeters=options["radius"])
                if options["retry_generation"]:
                    row["retryGeneration"] = options["retry_generation"]
                try:
                    place, routes = enrich_google_reference(project, reviewer, task.pk, options["radius"])
                    row.update(status="enriched", location=place["location"], routes=routes)
                except ValueError as exc:
                    row.update(status="failed", error=str(exc)[:300])
                time.sleep(options["delay_seconds"])
            report["projects"] = [item for item in report["projects"] if item["projectId"] != project_id] + [row]
            report["updatedAt"] = timezone.now().isoformat()
            write_report(path, report)
        report["state"] = "paused" if paused else "completed"
        report["counts"] = {state: sum(row["status"] == state for row in report["projects"])
            for state in {row["status"] for row in report["projects"]}}
        report["updatedAt"] = timezone.now().isoformat()
        write_report(path, report)
        self.stdout.write(f"Google project routes {report['state']}: {report['counts']}")

    @staticmethod
    def _reviewer(username):
        users = get_user_model().objects.filter(is_active=True, is_staff=True)
        if username:
            user = users.filter(**{get_user_model().USERNAME_FIELD: username}).first()
        else:
            superusers = list(users.filter(is_superuser=True)[:2])
            if len(superusers) != 1:
                raise CommandError("Specify --reviewer because there is not exactly one active superuser.")
            user = superusers[0]
        if not user or not user.has_perm("platform_api.change_project"):
            raise CommandError("--reviewer must identify active staff with project change permission.")
        return user