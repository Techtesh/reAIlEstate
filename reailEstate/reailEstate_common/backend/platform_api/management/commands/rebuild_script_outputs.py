"""Local maintenance: backup first, then replace unreviewed outputs per project."""

import hashlib
import json
import os
import subprocess
import zipfile
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Q
from django.test.utils import override_settings
from django.utils import timezone

from realestate_common.reports import write_json_report

from platform_api.artifact_storage import storage
from platform_api.floor_plan_rules import RuleBasedPlanProvider
from platform_api.models import AuditEvent, EvidenceDocument, ExtractionJob, FactCandidate, FactConflict, FloorPlan, FloorPlanAsset, IngestionRun, Operation, OutboxMessage, Project, ProjectFact
from platform_api.workers import process_document


def fingerprint(key):
    digest, length = hashlib.sha256(), 0
    with storage.open(key, "rb") as content:
        for block in iter(lambda: content.read(1024 * 1024), b""):
            digest.update(block)
            length += len(block)
    return digest.hexdigest(), length


def generated_keys(project):
    keys = set(project.floor_plan_assets.values_list("object_key", flat=True))
    for summary in project.ingestion_runs.values_list("summary", flat=True):
        keys.update(item["objectKey"] for item in summary.get("outputs", {}).values() if item.get("objectKey"))
    return keys


def ineligible(project):
    if project.status == "published" or ProjectFact.objects.filter(project=project).exists():
        return "PUBLISHED_DATA_PRESERVED"
    replaceable_plan_states = {"needs_review", "gen_with_ai_needs_review", "script_segment_needs_review"}
    if project.floor_plans.exclude(review_state__in=replaceable_plan_states).exists():
        return "REVIEWED_PLANS_PRESERVED"
    if project.fact_candidates.exclude(review_state="unreviewed").exists():
        return "REVIEWED_FACTS_PRESERVED"
    if project.review_tasks.exclude(state="open").exists() or FactConflict.objects.filter(project=project).exclude(state="open").exists():
        return "REVIEW_HISTORY_PRESERVED"
    for document in project.documents.all():
        if document.scan_status != "clean" or document.status not in {"needs_review", "approved", "quarantined", "extraction_failed"}:
            return "RECORDED_SCAN_OR_STATE_UNSUITABLE"
        try:
            if fingerprint(document.object_key) != (document.sha256, document.byte_length):
                return "SOURCE_INTEGRITY_MISMATCH"
        except OSError:
            return "SOURCE_MISSING_OR_UNREADABLE"
    return None


class RecordedScanner:
    def __init__(self, document):
        self.sha256, self.length = document.sha256, document.byte_length

    def scan(self, source):
        source.seek(0)
        digest, length = hashlib.sha256(), 0
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
            length += len(block)
        source.seek(0)
        if length != self.length or digest.hexdigest() != self.sha256:
            raise ValueError("SOURCE_INTEGRITY_MISMATCH")
        return "clean"  # Explicit user-authorized reuse, not a fresh malware scan.


class Command(BaseCommand):
    help = "Back up local data and rebuild unreviewed project outputs using scripts only. Requires exclusive maintenance window."

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true", help="Without this flag, print inventory only.")
        parser.add_argument("--reuse-recorded-scans", action="store_true", help="Explicitly authorize recorded-clean hash-bound scan reuse in DEBUG only.")
        parser.add_argument("--backup-dir", required=True, help="New private backup/report directory outside media storage.")

    def handle(self, *args, **options):
        if (not settings.DEBUG or settings.ENVIRONMENT not in {"local", "development"}
                or connection.settings_dict["HOST"] not in {"localhost", "127.0.0.1", "::1"}):
            raise CommandError("This maintenance operation is restricted to local DEBUG PostgreSQL databases.")
        if OutboxMessage.objects.filter(published_at__isnull=True, dead_lettered_at__isnull=True).exists():
            raise CommandError("Pending work exists. Stop workers and resolve it before rebuilding.")
        projects = list(Project.objects.filter(documents__isnull=False).distinct().order_by("submitted_name", "id"))
        report = {"startedAt": timezone.now().isoformat(), "mode": "script", "freshMalwareScan": False,
                  "status": "preflight", "projects": [], "cleanupWarnings": []}
        selected = []
        for project in projects:
            reason = ineligible(project)
            entry = {"projectId": str(project.id), "name": project.submitted_name,
                     "documents": project.documents.count(), "status": "skipped" if reason else "ready", "reason": reason}
            report["projects"].append(entry)
            if reason is None:
                selected.append((project, entry))
        self.stdout.write(f"Eligible projects: {len(selected)}/{len(projects)}; original PDFs and project/account records will be preserved.")
        if not options["execute"]:
            self.stdout.write(json.dumps(report, indent=2))
            return
        if not options["reuse_recorded_scans"]:
            raise CommandError("Explicit --reuse-recorded-scans consent is required for this local operation.")
        directory = Path(options["backup_dir"]).resolve()
        if directory.exists():
            raise CommandError("Backup directory already exists; choose a new directory.")
        directory.mkdir(parents=True)
        lock = Path(settings.WORKSPACE_DATA_ROOT) / ".script-rebuild.lock"
        try:
            with lock.open("x", encoding="utf-8") as marker:
                marker.write(str(directory))
        except FileExistsError as exc:
            raise CommandError("Another rebuild lock exists. Inspect that run before continuing.") from exc
        try:
            self._backup(directory, report)
            report["status"] = "running"
            self._save(directory, report)
            with override_settings(FLOOR_PLAN_EXTRACTION_MODE="script", PROJECT_ENRICHMENT_BACKEND=""):
                for index, (project, entry) in enumerate(selected, 1):
                    self.stdout.write(f"[{index}/{len(selected)}] {project.submitted_name}")
                    self.stdout.flush()
                    try:
                        self._rebuild(project, entry, directory, report)
                    except Exception as exc:
                        entry.update(status="failed_previous_data_preserved", errorCode=type(exc).__name__)
                        self.stdout.write(f"  Failed: {type(exc).__name__}; previous data preserved.")
                    self._save(directory, report)
            report["completedAt"] = timezone.now().isoformat()
            report["status"] = "completed" if all(item["status"] == "completed" for item in report["projects"]) else "completed_with_skips_or_failures"
            report["totals"] = {"projectsCompleted": sum(item["status"] == "completed" for item in report["projects"]),
                                "assetsNow": FloorPlanAsset.objects.count(), "plansNow": FloorPlan.objects.count(),
                                "runsNow": IngestionRun.objects.count(), "originalDocuments": EvidenceDocument.objects.count()}
            self._save(directory, report)
            self.stdout.write(json.dumps(report["totals"], indent=2))
            self.stdout.write(f"Report: {directory / 'rebuild-report.json'}")
        finally:
            lock.unlink(missing_ok=True)

    def _save(self, directory, report):
        write_json_report(directory / "rebuild-report.json", report, default=str)

    def _backup(self, directory, report):
        self.stdout.write("Backing up database and referenced source/derived files before any cleanup...")
        self.stdout.flush()
        database = connection.settings_dict
        environment = os.environ.copy()
        for name, value in {
            "PGDATABASE": database["NAME"], "PGUSER": database["USER"],
            "PGPASSWORD": database["PASSWORD"], "PGHOST": database["HOST"],
            "PGPORT": database["PORT"],
            "PGSSLMODE": database.get("OPTIONS", {}).get("sslmode", "prefer"),
            "PGCONNECT_TIMEOUT": database.get("OPTIONS", {}).get("connect_timeout", 10),
        }.items():
            environment[name] = str(value)
        archive_path = directory / "database.dump"
        try:
            subprocess.run([
                "pg_dump", "--format=custom", "--no-owner", "--no-privileges",
                "--file", str(archive_path),
            ], env=environment, check=True, capture_output=True)
            subprocess.run(["pg_restore", "--list", str(archive_path)],
                           check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            raise CommandError("PostgreSQL backup failed; install matching pg_dump/pg_restore tools and check the connection. Nothing was cleaned.") from None
        keys = set(EvidenceDocument.objects.values_list("object_key", flat=True))
        keys.update(FloorPlanAsset.objects.values_list("object_key", flat=True))
        for summary in IngestionRun.objects.values_list("summary", flat=True):
            keys.update(item["objectKey"] for item in summary.get("outputs", {}).values() if item.get("objectKey"))
        manifest = []
        with zipfile.ZipFile(directory / "media.zip", "x", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for index, key in enumerate(sorted(keys)):
                if not storage.exists(key):
                    manifest.append({"key": key, "missing": True})
                    continue
                archive_name = f"objects/{index:06d}"
                digest, size = hashlib.sha256(), 0
                with storage.open(key, "rb") as source, archive.open(archive_name, "w", force_zip64=True) as target:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        target.write(block)
                        digest.update(block)
                        size += len(block)
                manifest.append({"key": key, "archiveName": archive_name, "sha256": digest.hexdigest(), "size": size,
                                 "root": "workspace" if key.startswith(("user/media/", "output/media/")) else "legacy_media"})
        with zipfile.ZipFile(directory / "media.zip") as archive:
            if archive.testzip() is not None:
                raise CommandError("Backup verification failed; nothing was cleaned.")
        (directory / "media-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        report["backup"] = {"database": "database.dump", "media": "media.zip", "manifest": "media-manifest.json",
                            "verified": True, "fileCount": sum(not item.get("missing", False) for item in manifest)}
        self._save(directory, report)

    def _rebuild(self, project, entry, directory, report):
        old_keys = generated_keys(project)
        with transaction.atomic():
            project = Project.objects.select_for_update().get(id=project.id)
            reason = ineligible(project)
            if reason:
                raise ValueError(reason)
            documents = list(project.documents.select_for_update().order_by("created_at"))
            ids = [str(document.id) for document in documents]
            if OutboxMessage.objects.filter(event_type="document.processing.requested", payload__documentId__in=ids,
                    published_at__isnull=True, dead_lettered_at__isnull=True).exists():
                raise ValueError("PROJECT_BECAME_BUSY")
            run_ids = [str(value) for value in project.ingestion_runs.values_list("id", flat=True)]
            workflow_ids = [str(value) for value in project.agent_workflow_runs.values_list("id", flat=True)]
            plan_ids = [str(value) for value in project.floor_plans.values_list("id", flat=True)]
            tasks = project.review_tasks.filter(Q(candidate__document__in=documents)
                | Q(resolution__ingestionRunId__in=run_ids) | Q(resolution__workflowRunId__in=workflow_ids)
                | Q(resolution__floorPlanId__in=plan_ids))
            # Unlinked legacy insufficient-data tasks also belong to extraction, not publication.
            tasks = project.review_tasks.filter(Q(id__in=tasks.values("id")) | Q(task_type="insufficient_data"))
            tasks.delete()
            FactConflict.objects.filter(project=project, state="open").delete()
            FactCandidate.objects.filter(project=project, document__in=documents, review_state="unreviewed").delete()
            project.floor_plans.all().delete()
            project.floor_plan_assets.all().delete()
            project.ingestion_runs.all().delete()  # Cascades chunks and workflows/steps after PROTECT references removed.
            ExtractionJob.objects.filter(document__in=documents).delete()
            Operation.objects.filter(kind="document_processing", result__documentId__in=ids).update(state="superseded")
            runs = []
            for document in documents:
                document.status = "quarantined"
                document.error_category = ""
                document.save(update_fields=["status", "error_category", "updated_at"])
                job = ExtractionJob.objects.create(document=document, extractor_version="project-output-1.0", status="queued")
                operation = Operation.objects.create(owner=document.uploaded_by, kind="document_processing", state="pending",
                    result={"documentId": str(document.id)})
                processed = process_document(document.id, scanner=RecordedScanner(document), extractor=RuleBasedPlanProvider())
                run = processed.ingestion_runs.order_by("-created_at").first()
                if processed.error_category or not run or run.status != "completed" or not run.summary.get("outputs"):
                    raise ValueError("SCRIPT_REBUILD_FAILED")
                run.summary["scanProvenance"] = {"type": "user_authorized_recorded_status_reuse", "freshScan": False,
                                                 "sha256": document.sha256}
                run.save(update_fields=["summary", "updated_at"])
                runs.append({"documentId": str(document.id), "runId": str(run.id), "operationId": str(operation.id),
                             "outputs": {name: item["uri"] for name, item in run.summary["outputs"].items()},
                             "imageCounts": {name: len(items) for name, items in run.summary["projectOutput"]["images"].items()}})
            AuditEvent.objects.create(actor=None, action="project.script_outputs_rebuilt", entity_type="Project", entity_id=project.id,
                correlation_id=uuid4(), reason_code="USER_REQUESTED_CLEAN_REBUILD", metadata={"backupDirectory": str(directory),
                    "freshScan": False, "mode": "script", "documentCount": len(documents)})
        entry.update(status="completed", runs=runs, oldGeneratedFiles=len(old_keys))
        # Old artifacts are backed up and new DB outputs committed before deletion.
        current = generated_keys(project) | set(EvidenceDocument.objects.values_list("object_key", flat=True))
        removed = 0
        for key in old_keys - current:
            if not key.startswith(("derived/", "output/media/")):
                report["cleanupWarnings"].append({"key": key, "reason": "UNEXPECTED_PREFIX_PRESERVED"})
                continue
            try:
                if storage.exists(key):
                    storage.delete(key)
                    removed += 1
            except OSError:
                report["cleanupWarnings"].append({"key": key, "reason": "FILE_DELETE_FAILED"})
        entry["oldGeneratedFilesRemoved"] = removed
        self.stdout.write(f"  Completed {len(runs)} document(s); removed {removed} old generated files.")
        self.stdout.flush()