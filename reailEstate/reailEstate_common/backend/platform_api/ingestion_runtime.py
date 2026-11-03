"""Process-safe job ownership and nonsecret, per-document progress events."""

import hashlib
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.utils import timezone


logger = logging.getLogger("platform_api.ingestion")
_progress = ContextVar("ingestion_progress", default=None)


class WorkAlreadyClaimed(Exception):
    pass


@contextmanager
def work_lock(namespace, identifier):
    """Session advisory lock: no open transaction during I/O; released on process/connection death.

    PostgreSQL must be accessed directly (not through a transaction-mode pooler).
    """
    digest = hashlib.sha256(f"{namespace}:{identifier}".encode()).digest()
    key = int.from_bytes(digest[:8], "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        acquired = cursor.fetchone()[0]
    if not acquired:
        raise WorkAlreadyClaimed(str(identifier))
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [key])


def event(stage, **details):
    from .queue_monitor import report_progress
    report_progress(stage, details)
    progress = _progress.get()
    if progress:
        progress.emit(stage, **details)


@contextmanager
def model_request_slot(selection):
    """Bound model calls across worker AND web processes sharing this PostgreSQL primary."""
    limit = max(1, min(int(getattr(settings, "VISION_MAX_CONCURRENT_REQUESTS", 8)), 16))
    deadline = time.monotonic() + 300
    while True:
        for slot in range(limit):
            # One shared server-side budget, even when callers select different profile aliases.
            lock = work_lock("model-slot", slot)
            try:
                lock.__enter__()
            except WorkAlreadyClaimed:
                continue
            try:
                yield
            finally:
                lock.__exit__(None, None, None)
            return
        if time.monotonic() >= deadline:
            from .integrations import ProviderUnavailable
            raise ProviderUnavailable("Vision concurrency budget is busy.")
        event("waiting_model_slot", maxConcurrentRequests=limit)
        time.sleep(1)


class DocumentProgress:
    """One worker owns one status file; heartbeat never opens a database connection."""

    def __init__(self, document_id, context=None):
        self.document_id = str(document_id)
        self.started = time.monotonic()
        self.state = {"documentId": self.document_id, "pid": os.getpid(), **(context or {})}
        self.guard = threading.Lock()
        self.stop = threading.Event()
        root = os.environ.get("INGESTION_PROGRESS_DIR")
        self.path = Path(root) / (self.document_id + ".json") if root else None

    def emit(self, stage, **details):
        with self.guard:
            self.state.update(stage=stage, updatedAt=timezone.now().isoformat(),
                              elapsedSeconds=round(time.monotonic() - self.started, 2), **details)
            self._write()
            logger.info(json.dumps(self.state, ensure_ascii=True))

    def _write(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
            for attempt in range(10):
                try:
                    temporary.replace(self.path)
                    return
                except PermissionError:
                    if attempt == 9:
                        logger.warning(json.dumps({
                            "event": "progress_checkpoint_deferred",
                            "documentId": self.document_id,
                            "errorCode": "WINDOWS_FILE_LOCK",
                        }))
                        return
                    time.sleep(0.1 * (attempt + 1))

    def _heartbeat(self):
        while not self.stop.wait(15):
            with self.guard:
                self.state.update(heartbeatAt=timezone.now().isoformat(),
                                  elapsedSeconds=round(time.monotonic() - self.started, 2))
                self._write()
                logger.info(json.dumps(dict(self.state, event="heartbeat")))

    def __enter__(self):
        self.token = _progress.set(self)
        self.emit("claimed")
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.stop.set()
        self.thread.join()
        if exc_type:
            self.emit("failed", errorCode=exc_type.__name__)
        _progress.reset(self.token)