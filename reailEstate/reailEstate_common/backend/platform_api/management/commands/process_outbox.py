import time

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from platform_api.jobs.events import OUTBOX_EVENT_GROUPS
from platform_api.models import OutboxMessage, Project
from platform_api.workers import process_pending_outbox


class Command(BaseCommand):
    help = "Process pending durable outbox messages for recommendations, documents and media."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process one batch and exit.")
        parser.add_argument("--owner-id", type=int, help="Only extraction jobs queued by this user.")
        parser.add_argument("--project-id", help="Only extraction jobs for this project, including new child jobs.")
        parser.add_argument("--drain", action="store_true", help="Exit after pending work, including child jobs and retries, finishes.")
        parser.add_argument("--worker-id", help="Existing local worker launch record.")
        parser.add_argument("--limit", type=int, default=20, help="Maximum messages per batch.")
        parser.add_argument("--poll-seconds", type=float, default=2.0, help="Delay between empty polls.")
        parser.add_argument("--event-type", choices=list(OUTBOX_EVENT_GROUPS), default="all",
                help="Use documents for ingestion only, or media for page/crop/units evaluation; all is unfiltered.")

    def handle(self, *args, **options):
        from platform_api.queue_monitor import require_monitor_access, worker_session
        owner = None
        if options["owner_id"]:
            owner = get_user_model().objects.get(pk=options["owner_id"])
            require_monitor_access(owner, run=True)
            if options["event_type"] not in {"media", "documents", "extraction"}:
                raise CommandError("Owner filtering supports extraction events only.")
        project = None
        if options["project_id"]:
            if options["event_type"] not in {"media", "documents", "extraction"}:
                raise CommandError("Project filtering supports extraction events only.")
            project = Project.objects.get(pk=options["project_id"])
            if project.status in {"retired", "merged"}:
                raise CommandError("Choose an active project.")
        with worker_session(options["event_type"], owner, options["worker_id"], project=project):
            self.process(options, owner, project)

    def process(self, options, owner, project=None):
        from platform_api.queue_monitor import dispatch_waiting_queues, owned_jobs, project_jobs, require_monitor_access
        limit = max(1, min(options["limit"], 500))
        poll_seconds = max(0.1, options["poll_seconds"])
        event_types = OUTBOX_EVENT_GROUPS[options["event_type"]]
        while True:
            dispatch_waiting_queues()
            if owner:
                owner.refresh_from_db()
                require_monitor_access(owner, run=True)
            if project:
                project.refresh_from_db()
                if project.status in {"retired", "merged"}:
                    return
                pending = project_jobs(project)
            else:
                pending = owned_jobs(owner) if owner else OutboxMessage.objects.all()
            pending = pending.filter(published_at__isnull=True, dead_lettered_at__isnull=True)
            if event_types is not None:
                pending = pending.filter(event_type__in=event_types)
            if options["drain"] and not pending.exists():
                return
            message_ids = list(pending.filter(Q(available_after__isnull=True) | Q(available_after__lte=timezone.now()))
                .order_by("occurred_at").values_list("id", flat=True)[:limit])
            results = process_pending_outbox(limit=limit, message_ids=message_ids)
            if any(results.values()):
                self.stdout.write(
                    f"processed={results['processed']} retry={results['retry']} failed={results['failed']}"
                )
            if options["once"]:
                return
            time.sleep(poll_seconds)
