"""Spawn-safe document processes. Each process performs at most one model request at a time."""

import os
import signal
import time


def initialize_worker(progress_directory):
    # Windows spawn must initialize Django before importing any models.
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    os.environ["INGESTION_PROGRESS_DIR"] = progress_directory
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Coordinator drains active work on Ctrl+C.
    import django
    django.setup()


def extract_one(payload):
    from django.contrib.auth import get_user_model
    from django.db import connections
    from platform_api.floor_plan_vision import get_floor_plan_vision_provider
    from platform_api.ingestion_runtime import work_lock, WorkAlreadyClaimed
    from platform_api.management.commands.import_sample_corpus import Command, _CleanTestScanner
    from platform_api.models import EvidenceDocument
    from platform_api.workers import process_document

    started = time.monotonic()
    provider = None
    row = {"documentId": payload["documentId"], "state": "failed"}
    try:
        with work_lock("document", payload["documentId"]):
            document = EvidenceDocument.objects.get(pk=payload["documentId"])
            uploader = get_user_model().objects.get(pk=payload["uploaderId"])
            if document.status == "extracting":
                # Ownership lock proves the previous owner no longer has the session.
                document.ingestion_runs.filter(status="extracting").update(status="failed", error_code="WORKER_INTERRUPTED")
                document.status = "extraction_failed"
                document.save(update_fields=["status", "updated_at"])
            Command._prepare_processing(document, uploader, payload["sourcePath"], payload["scanner"], payload["selection"])
            provider = get_floor_plan_vision_provider(payload["selection"])
            scanner = _CleanTestScanner() if payload["scanner"] == "local_test_bypass" else None
            result = process_document(
                document.id, scanner=scanner, extractor=provider, vision_selection=payload["selection"],
                progress_context={"projectId": payload["projectId"], "projectKey": payload["projectKey"],
                                  "projectName": payload["projectName"]},
            )
            Command._outcome(row, result)
    except WorkAlreadyClaimed:
        row.update(state="failed", errorCode="ALREADY_PROCESSING")
    except Exception as exc:
        row.update(state="failed", errorCode=type(exc).__name__)
    finally:
        if hasattr(provider, "close"):
            provider.close()
        connections.close_all()
    row["durationSeconds"] = round(time.monotonic() - started, 2)
    return row