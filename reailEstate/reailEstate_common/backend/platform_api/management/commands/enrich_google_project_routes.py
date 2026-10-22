"""Explicitly review locations and persist station routes in bounded batches."""

import time
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from filelock import FileLock, Timeout

from platform_api.external_sources_admin import enrich_google_reference, manual_reference_details, refresh_google_routes
from platform_api.google_route_batches import RouteBatchResult, drain_route_batches
from platform_api.models import ReviewTask
from .refresh_google_project_places import active_projects, read_report, report_default, write_report

def google_references(*, include_manual=False):
    references = {}
    for task in ReviewTask.objects.filter(task_type="external_sources").order_by("created_at", "pk"):
        if isinstance(task.resolution, dict) and (task.resolution.get("googlePlaceId") or (
            include_manual and manual_reference_details(task.resolution.get("manualLocation")))):
            references[task.project_id] = task
    return references


def has_available_route(row):
    return any(station.get(mode, {}).get("status") == "available"
        for group in row.get("routes", {}).get("groups", []) for station in group.get("stations", [])
        for mode in ("walking", "driving"))


class Command(BaseCommand):
    help = "Prefetch or accept Google locations and save station routes (maximum five provider projects per batch)."

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true", help="Confirm review and apply Google locations.")
        parser.add_argument("--prefetch-only", action="store_true",
            help="Save routes for Google references and manual pins without approving or changing project locations.")
        parser.add_argument("--resume", action="store_true")
        parser.add_argument("--retry-failed", action="store_true")
        parser.add_argument("--retry-generation", default="")
        parser.add_argument("--batch-size", type=int, default=5)
        parser.add_argument("--drain", action="store_true", help="Resume bounded batches until complete; stop if the first batch returns no durations.")
        parser.add_argument("--max-batches", type=int, default=100)
        parser.add_argument("--radius", type=int, choices=(5000, 10000, 20000), default=10000)
        parser.add_argument("--delay-seconds", type=float, default=0.25)
        parser.add_argument("--reviewer")
        parser.add_argument("--report", type=Path, default=report_default("google-project-routes.json"))

    def handle(self, *args, **options):
        if not options["execute"]:
            self.stdout.write("Preview only: --execute calls Google APIs and saves routes. "
                + ("Location approvals remain unchanged." if options["prefetch_only"] else
                   "Without --prefetch-only, execution also confirms review and applies project locations."))
            return
        if not getattr(settings, "GOOGLE_MAPS_API_KEY", ""):
            raise CommandError("GOOGLE_MAPS_API_KEY is not configured in this process.")
        if not 1 <= options["batch_size"] <= 5:
            raise CommandError("--batch-size must be between 1 and 5.")
        if not 1 <= options["max_batches"] <= 100:
            raise CommandError("--max-batches must be between 1 and 100.")
        if not 0.1 <= options["delay_seconds"] <= 10:
            raise CommandError("--delay-seconds must be between 0.1 and 10.")
        if options["retry_failed"] and not options["retry_generation"]:
            raise CommandError("--retry-failed requires --retry-generation.")
        reviewer = self._reviewer(options.get("reviewer"))
        path = options["report"].expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with FileLock(str(path.with_suffix(".lock")), timeout=0):
                if options["drain"]:
                    def run_batch(resume):
                        return self._run(path, reviewer, options | {"resume": options["resume"] or resume})

                    try:
                        drain_route_batches(run_batch, max_batches=options["max_batches"])
                    except ValueError as exc:
                        raise CommandError(str(exc)) from exc
                else:
                    self._run(path, reviewer, options)
        except Timeout:
            raise CommandError("Another route enrichment is already using this report.") from None

    def _run(self, path, reviewer, options):
        old = read_report(path) if options["resume"] else {}
        previous = {row["projectId"]: row for row in old.get("projects", [])}
        references = google_references(include_manual=options["prefetch_only"])
        projects = list(active_projects())
        project_ids = {str(project.pk) for project in projects}
        report = {"state": "running", "radiusMeters": options["radius"],
            "projects": [row for key, row in previous.items() if key in project_ids]}
        attempted = 0
        attempted_rows = []
        paused = False
        for project in projects:
            project_id = str(project.pk)
            task = references.get(project.pk)
            old_row = previous.get(project_id, {})
            same_reference = (task and old_row.get("reviewTaskId") == str(task.pk)
                and old_row.get("googlePlaceId") == task.resolution.get("googlePlaceId")
                and old_row.get("manualLocation") == task.resolution.get("manualLocation")
                and old_row.get("prefetchOnly", False) == options["prefetch_only"]
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
                row.update(reviewTaskId=str(task.pk), googlePlaceId=task.resolution.get("googlePlaceId"),
                    manualLocation=task.resolution.get("manualLocation"),
                    prefetchOnly=options["prefetch_only"], radiusMeters=options["radius"])
                if options["retry_generation"]:
                    row["retryGeneration"] = options["retry_generation"]
                try:
                    if options["prefetch_only"]:
                        routes = refresh_google_routes(project, reviewer, task.pk, options["radius"])
                        location = routes["origin"]
                    else:
                        place, routes = enrich_google_reference(project, reviewer, task.pk, options["radius"])
                        location = place["location"]
                    row.update(status="enriched", location=location, routes=routes)
                except ValueError as exc:
                    row.update(status="failed", error=str(exc)[:300])
                attempted_rows.append(row)
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
        return RouteBatchResult(report, attempted_rows)

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