from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from platform_api.floor_plan_visibility import HIDDEN_REVIEW_STATES
from platform_api.floor_plan_vision import resolve_vision_selection
from platform_api.layout_reanalysis import queue_layout_evaluation
from platform_api.models import FloorPlan


class Command(BaseCommand):
    help = "Preview or force-queue AI extraction for every eligible saved unit layout."

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true", help="Create or retry durable evaluation jobs.")
        parser.add_argument("--vision-profile-id", help="Configured vision profile; defaults to the server selection.")
        parser.add_argument("--limit", type=int, help="Maximum eligible layouts to inspect, for staged runs.")

    def handle(self, *args, **options):
        if not settings.DEBUG or str(getattr(settings, "ENVIRONMENT", "")).casefold() not in {"local", "development"}:
            raise CommandError("This bulk command is restricted to local/development environments.")
        selection = resolve_vision_selection(options["vision_profile_id"])
        layouts = FloorPlan.objects.filter(
            plan_scope="flat", crop_remaining=False, asset__status="ready", document__scan_status="clean",
        ).exclude(
            review_state__in=HIDDEN_REVIEW_STATES,
        ).exclude(
            project__status__in={"retired", "merged"},
        ).select_related("project", "document", "asset").order_by("project_id", "id")
        if options["limit"] is not None:
            if options["limit"] < 1:
                raise CommandError("--limit must be at least 1.")
            layouts = layouts[:options["limit"]]
        candidates = list(layouts)
        self.stdout.write(f"eligible={len(candidates)} profile={selection['profileId']}")
        if not options["execute"]:
            self.stdout.write("Preview only. Re-run with --execute to queue evaluations.")
            return

        counts = {"queued": 0, "reused": 0, "skipped": 0}
        for index, plan in enumerate(candidates, 1):
            try:
                message, operation = queue_layout_evaluation(
                    plan, vision_selection=selection, retry=True, force=True,
                )
            except (FloorPlan.DoesNotExist, ValueError) as exc:
                counts["skipped"] += 1
                self.stderr.write(f"[{index}/{len(candidates)}] skipped {plan.pk}: {exc}")
                continue
            if operation.state == "pending" and message.published_at is None:
                counts["queued"] += 1
            else:
                counts["reused"] += 1
        self.stdout.write(" ".join(f"{key}={value}" for key, value in counts.items()))