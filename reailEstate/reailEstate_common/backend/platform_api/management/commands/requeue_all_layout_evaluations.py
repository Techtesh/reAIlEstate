from uuid import UUID

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from platform_api.bulk_layout_evaluations import EvaluationMode, eligible_layouts, queue_layout_batch
from platform_api.floor_plan_vision import resolve_vision_selection


class Command(BaseCommand):
    help = "Preview or force-queue AI extraction for every eligible saved unit layout."

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true", help="Create or retry durable evaluation jobs.")
        parser.add_argument("--vision-profile-id", help="Configured vision profile; defaults to the server selection.")
        parser.add_argument("--limit", type=int, help="Maximum eligible layouts to inspect, for staged runs.")
        parser.add_argument("--project-id", type=UUID, help="Restrict the batch to one project.")
        parser.add_argument("--mode", choices=[mode.value for mode in EvaluationMode], default=EvaluationMode.FORCE.value,
            help="new preserves prior results and failures; retry-failed also retries failures; force also reruns completed results (legacy default).")

    def handle(self, *args, **options):
        if not settings.DEBUG or str(getattr(settings, "ENVIRONMENT", "")).casefold() not in {"local", "development"}:
            raise CommandError("This bulk command is restricted to local/development environments.")
        selection = resolve_vision_selection(options["vision_profile_id"])
        try:
            layouts = eligible_layouts(project_id=options["project_id"], limit=options["limit"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        candidates = list(layouts)
        self.stdout.write(f"eligible={len(candidates)} profile={selection['profileId']} mode={options['mode']}")
        if not options["execute"]:
            self.stdout.write("Preview only. Re-run with --execute to queue evaluations.")
            return

        counts = {"queued": 0, "reused": 0, "skipped": 0}
        outcomes = queue_layout_batch(candidates, vision_selection=selection, mode=options["mode"])
        for index, outcome in enumerate(outcomes, 1):
            counts[outcome.status] += 1
            if outcome.error:
                self.stderr.write(f"[{index}/{len(candidates)}] skipped {outcome.plan_id}: {outcome.error}")
        self.stdout.write(" ".join(f"{key}={value}" for key, value in counts.items()))