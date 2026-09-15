import time

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from platform_api.jobs.events import OUTBOX_EVENT_GROUPS
from platform_api.models import OutboxMessage
from platform_api.workers import process_pending_outbox


class Command(BaseCommand):
    help = "Process pending durable outbox messages for recommendations, documents and media."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process one batch and exit.")
        parser.add_argument("--limit", type=int, default=20, help="Maximum messages per batch.")
        parser.add_argument("--poll-seconds", type=float, default=2.0, help="Delay between empty polls.")
        parser.add_argument("--event-type", choices=list(OUTBOX_EVENT_GROUPS), default="all",
                help="Use documents for ingestion only, or media for page/crop/units evaluation; all is unfiltered.")

    def handle(self, *args, **options):
        limit = max(1, min(options["limit"], 500))
        poll_seconds = max(0.1, options["poll_seconds"])
        event_types = OUTBOX_EVENT_GROUPS[options["event_type"]]
        while True:
            if event_types is not None and len(event_types) > 1:
                # The existing worker accepts one event type or explicit IDs. Select
                # one shared ordered batch, respecting retry delays on every poll;
                # the worker still owns claims, readiness rechecks and backoff.
                message_ids = list(
                    OutboxMessage.objects.filter(
                        event_type__in=event_types,
                        published_at__isnull=True,
                        dead_lettered_at__isnull=True,
                    )
                    .filter(Q(available_after__isnull=True) | Q(available_after__lte=timezone.now()))
                    .order_by("occurred_at")
                    .values_list("id", flat=True)[:limit]
                )
                results = process_pending_outbox(limit=limit, message_ids=message_ids)
            else:
                event_type = None if event_types is None else event_types[0]
                results = process_pending_outbox(limit=limit, event_type=event_type)
            if any(results.values()):
                self.stdout.write(
                    f"processed={results['processed']} retry={results['retry']} failed={results['failed']}"
                )
            if options["once"]:
                return
            time.sleep(poll_seconds)
