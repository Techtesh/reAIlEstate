import math
import threading

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from platform_api.automatic_queue import run_automatic_queue
from platform_api.ingestion_runtime import WorkAlreadyClaimed


class Command(BaseCommand):
    help = "Continuously start eligible extraction jobs when shared worker capacity is available."

    def add_arguments(self, parser):
        parser.add_argument("--poll-seconds", type=float, default=2)

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError("Automatic concurrent extraction requires PostgreSQL.")
        if not math.isfinite(options["poll_seconds"]) or options["poll_seconds"] < 0.1:
            raise CommandError("--poll-seconds must be a finite number of at least 0.1.")
        stop = threading.Event()
        self.stdout.write("Automatic extraction dispatcher starting; waiting for eligible queued jobs.")
        try:
            run_automatic_queue(stop, poll_seconds=options["poll_seconds"])
        except WorkAlreadyClaimed:
            raise CommandError("An automatic extraction dispatcher is already running.") from None
        except KeyboardInterrupt:
            stop.set()
            self.stdout.write("Dispatcher stopped.")
