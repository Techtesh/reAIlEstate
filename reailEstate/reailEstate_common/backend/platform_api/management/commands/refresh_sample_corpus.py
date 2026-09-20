"""Checkpointed corpus coordinator; only this process writes the aggregate report."""

import hashlib
import json
import multiprocessing
import signal
import time
from collections import Counter, defaultdict, deque
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import CommandError
from django.db import connection, connections
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from filelock import FileLock, Timeout

from platform_api.concurrent_ingestion import extract_one, initialize_worker
from platform_api.floor_plan_vision import resolve_vision_selection
from platform_api.management.commands.import_sample_corpus import Command as SerialCommand
from platform_api.models import Organization, OrganizationMembership, OutboxMessage
from platform_api.sample_corpus import categorize_samples, prioritize_project_sources
from platform_api.sample_reports import build_group_report


class Command(SerialCommand):
    help = "Refresh a corpus with 1–5 document processes, PostgreSQL ownership locks, and live progress."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("--workers", type=int, default=None)

    def handle(self, *args, **options):
        options["workers"] = options["workers"] or settings.INGESTION_MAX_WORKERS
        if not 1 <= options["workers"] <= settings.INGESTION_MAX_WORKERS:
            raise CommandError(f"--workers must be between 1 and {settings.INGESTION_MAX_WORKERS}.")
        if connection.vendor != "postgresql":
            raise CommandError("Concurrent refresh requires PostgreSQL. Use import_sample_corpus for SQLite.")
        if not all(options[key] for key in ("import_samples", "process", "reprocess", "username")):
            raise CommandError("Supply --import --process --reprocess --username for a corpus refresh.")
        if options["test_scanner"] and (not settings.DEBUG or settings.ENVIRONMENT not in {"local", "development"}):
            raise CommandError("--test-scanner is only permitted in local/development with DEBUG enabled.")
        lock_root = Path(settings.WORKSPACE_DATA_ROOT) / "output" / "reports" / ".locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        try:
            with FileLock(str(lock_root / "corpus-refresh.lock"), timeout=0):
                self._refresh(options)
        except Timeout:
            raise CommandError("Another corpus refresh is already running.") from None

    def _refresh(self, options):
        source = options["source"].expanduser().resolve()
        if not source.is_dir():
            raise CommandError("Source directory does not exist.")
        root = (Path(settings.WORKSPACE_DATA_ROOT) / "output" / "reports").resolve()
        path = options["report"] or Path("sample-corpus.json")
        path = (path if path.is_absolute() else root / path).resolve()
        if not path.is_relative_to(root):
            raise CommandError("Reports must be within output/reports.")
        stop_path = path.with_suffix(".stop")
        if stop_path.exists():
            raise CommandError(f"Remove the stop marker before resuming: {stop_path}")
        uploader = get_user_model().objects.filter(username=options["username"]).first()
        if not uploader:
            raise CommandError("Uploader does not exist.")
        if OutboxMessage.objects.filter(event_type="document.processing.requested", published_at__isnull=True,
                                        dead_lettered_at__isnull=True).exists():
            raise CommandError("Finish pending document outbox work before starting the corpus coordinator.")
        selection = resolve_vision_selection(options["vision_profile_id"])
        scanner = "local_test_bypass" if options["test_scanner"] else settings.DOCUMENT_SCANNER
        samples, ignored = categorize_samples(source, settings.MAX_UPLOAD_BYTES)
        requested_keys = {key.casefold().strip() for key in options["project_key"] if key.strip()}
        if requested_keys:
            samples = [sample for sample in samples if sample.project_key in requested_keys]
            missing = requested_keys - {sample.project_key for sample in samples}
            if missing:
                raise CommandError("Unknown project key(s): " + ", ".join(sorted(missing)))
        report = self._report(samples, ignored, source)
        report.update(sourceRoot=str(source), organization=options["organization"], visionSelection=selection,
                      scanner=scanner, reprocess=True, startedAt=timezone.now().isoformat(), state="staging",
                      workers=options["workers"], resumeStatuses=sorted(set(options["resume_status"])),
                      sources=[], projects=[], coordinatorPid=__import__("os").getpid())
        resume_statuses = set(options["resume_status"])
        if options["retry_failed"]:
            resume_statuses.update({"failed", "extraction_failed", "awaiting_visual_analysis"})
        report["resumeStatuses"] = sorted(resume_statuses)
        previous = {}
        if options["resume"]:
            if not path.exists():
                raise CommandError("--resume requires an existing report.")
            old = json.loads(path.read_text(encoding="utf-8"))
            for key in ("sourceRoot", "organization", "visionSelection", "scanner", "reprocess"):
                if old.get(key) != report[key]:
                    raise CommandError(f"Resume configuration mismatch: {key}")
            previous = {row["path"]: row for row in old.get("sources", [])}
            report["startedAt"] = old["startedAt"]
        organization, _ = Organization.objects.get_or_create(legal_name=options["organization"])
        OrganizationMembership.objects.get_or_create(organization=organization, user=uploader,
                                                     defaults={"role": "owner", "status": "active"})
        groups = defaultdict(list)
        for sample in samples:
            groups[sample.project_key].append(sample)
        project_groups = []
        pending = deque()
        aliases = defaultdict(list)
        for key, unordered_group in groups.items():
            group = prioritize_project_sources(unordered_group)
            project = self._project(organization, key, group)
            project_name = project.address.get("sampleCorpusName", project.submitted_name)
            rows, by_digest = [], {}
            for sample in group:
                relative = sample.path.relative_to(source).as_posix()
                row = {
                    "path": relative,
                    "sha256": sample.sha256,
                    "mediaType": sample.media_type,
                    "category": sample.category,
                    "sourceRole": sample.source_role,
                    "processingPriority": sample.processing_priority,
                    "projectId": str(project.id),
                    "projectKey": key,
                    "projectName": project_name,
                    "state": "queued",
                }
                rows.append(row)
                report["sources"].append(row)
                if not sample.eligible:
                    row.update(state="skipped", errorCode="MAX_UPLOAD_BYTES_EXCEEDED")
                    continue
                if sample.sha256 in by_digest:
                    canonical = by_digest[sample.sha256]
                    row.update(documentId=canonical.get("documentId"), duplicateOf=canonical["path"])
                    aliases[canonical["path"]].append(row)
                    continue
                by_digest[sample.sha256] = row
                try:
                    document, created = self._stage(project, sample, uploader)
                    row.update(documentId=str(document.id), imported=created)
                    old = previous.get(relative, {})
                    if resume_statuses and old.get("state") not in resume_statuses and document.status not in resume_statuses:
                        if old:
                            row.update(old, resumed=True, skippedByStatus=True)
                        else:
                            self._outcome(row, document)
                            row["skippedByStatus"] = True
                        continue
                    if (old.get("state") == "completed" and old.get("sha256") == sample.sha256
                            and old.get("documentId") == str(document.id)
                            and document.ingestion_runs.filter(id=old.get("ingestionRunId"), status="completed").exists()):
                        row.update(old, resumed=True)
                        # Old durations must not influence this run's rolling ETA.
                        row.pop("durationSeconds", None)
                    else:
                        # Recover a successful DB commit even if the coordinator died before its checkpoint.
                        run = document.ingestion_runs.order_by("-created_at").first()
                        if (options["resume"] and old.get("documentId") == str(document.id)
                                and old.get("sha256") == sample.sha256 and old.get("state") in {"queued", "processing"}
                                and run and run.status == "completed" and document.status in {"approved", "needs_review"}
                                and run.created_at >= parse_datetime(old.get("attemptStartedAt", report["startedAt"]))):
                            self._outcome(row, document)
                            row.update(resumed=True, recoveredCommittedResult=True)
                        else:
                            pending.append(row)
                except Exception as exc:
                    row.update(state="failed", errorCode=type(exc).__name__)
            project_groups.append((project, key, rows))
            # Preserve unvisited checkpoint entries during staging in case staging is interrupted.
            staged_paths = {item["path"] for item in report["sources"]}
            snapshot = dict(report, sources=report["sources"] + [item for name, item in previous.items() if name not in staged_paths])
            self._write_report(snapshot, path)
        for row in report["sources"]:
            self._aliases(row, aliases)
        progress_dir = path.parent / (path.stem + "-progress")
        report["state"] = "processing"
        self._write_report(report, path)
        self.stdout.write(f"Starting {options['workers']} processes; {len(pending)} documents pending; resume preserves completed outputs.")
        requested_stop = False

        def stop(signum, frame):
            nonlocal requested_stop
            requested_stop = True
            self.stdout.write("Graceful stop requested: finishing active documents, not scheduling more.")

        prior_handler = signal.signal(signal.SIGINT, stop)
        active = {}
        finished_groups = set()
        started_projects = set()
        timed = defaultdict(list)
        connections.close_all()  # Never share a parent's DB connection with worker processes.
        try:
            with ProcessPoolExecutor(max_workers=options["workers"], mp_context=multiprocessing.get_context("spawn"),
                    initializer=initialize_worker, initargs=(str(progress_dir),)) as executor:
                while pending or active:
                    requested_stop = requested_stop or stop_path.exists()
                    while pending and len(active) < options["workers"] and not requested_stop:
                        row = pending.popleft()
                        if row["projectKey"] not in started_projects:
                            started_projects.add(row["projectKey"])
                            project_rows = next(rows for _, key, rows in project_groups if key == row["projectKey"])
                            pending_count = sum(item["state"] == "queued" for item in project_rows)
                            self.stdout.write(
                                f"{timezone.now().isoformat()} PROJECT START "
                                f"[{row['projectName']}] key={row['projectKey']} "
                                f"projectId={row['projectId']} queuedDocuments={pending_count}"
                            )
                        row.update(state="processing", attemptStartedAt=timezone.now().isoformat())
                        row.pop("progress", None)
                        # Discard stale heartbeat from an interrupted attempt, not its result checkpoint.
                        (progress_dir / (row["documentId"] + ".json")).unlink(missing_ok=True)
                        payload = {"documentId": row["documentId"], "uploaderId": str(uploader.pk),
                                   "sourcePath": row["path"], "scanner": scanner, "selection": selection,
                                   "projectId": row["projectId"], "projectKey": row["projectKey"],
                                   "projectName": row["projectName"]}
                        active[executor.submit(extract_one, payload)] = row
                        self.stdout.write(
                            f"{timezone.now().isoformat()} DOCUMENT START "
                            f"[{row['projectName']}] {row['path']} documentId={row['documentId']}"
                        )
                    if not active and requested_stop:
                        break
                    self._write_report(report, path)
                    if active:
                        done, _ = wait(active, timeout=5, return_when=FIRST_COMPLETED)
                        for future in done:
                            row = active.pop(future)
                            try:
                                row.update(future.result())
                            except Exception as exc:
                                row.update(state="failed", errorCode=type(exc).__name__)
                            row["finishedAt"] = timezone.now().isoformat()
                            if row["state"] == "completed" and row.get("durationSeconds"):
                                timed[row["mediaType"]].append(row["durationSeconds"])
                            self._aliases(row, aliases)
                            self.stdout.write(
                                f"{row['finishedAt']} DOCUMENT {row['state'].upper()} "
                                f"[{row['projectName']}] {row['path']} "
                                f"documentId={row['documentId']} ({row.get('durationSeconds', 0):.1f}s)"
                            )
                    for row in active.values():
                        progress_path = progress_dir / (row["documentId"] + ".json")
                        if progress_path.exists():
                            try:
                                row["progress"] = json.loads(progress_path.read_text(encoding="utf-8"))
                            except (OSError, ValueError):
                                pass
                    for project, key, rows in project_groups:
                        if key in finished_groups or any(row["state"] in {"queued", "processing"} for row in rows):
                            continue
                        group_path = path.parent / (path.stem + "-projects") / (hashlib.sha256(key.encode()).hexdigest()[:16] + ".json")
                        self._write_report(build_group_report(project, key, rows), group_path)
                        report["projects"].append({"projectKey": key, "projectId": str(project.id),
                            "name": project.address.get("sampleCorpusName"), "sourceCount": len(rows),
                            "states": dict(Counter(row["state"] for row in rows)), "report": group_path.relative_to(root).as_posix()})
                        finished_groups.add(key)
                        self.stdout.write(
                            f"{timezone.now().isoformat()} PROJECT COMPLETE "
                            f"[{project.address.get('sampleCorpusName', project.submitted_name)}] "
                            f"key={key} projectId={project.id} states={dict(Counter(row['state'] for row in rows))}"
                        )
                    active_projects = sorted({row["projectKey"] for row in active.values()})
                    report.update(heartbeatAt=timezone.now().isoformat(), activeDocuments=len(active),
                                  queuedDocuments=len(pending), activeProjects=active_projects,
                                  states=dict(Counter(row["state"] for row in report["sources"])))
                    remaining = list(pending) + list(active.values())
                    report["etaSeconds"] = (round(sum(sum(timed[row["mediaType"]][-10:]) / len(timed[row["mediaType"]][-10:])
                        for row in remaining) / options["workers"]) if remaining
                        and all(len(timed[row["mediaType"]]) >= 3 for row in remaining) else None)
                    report["timingByMediaType"] = {kind: {"count": len(values), "meanSeconds": round(sum(values) / len(values), 2)}
                                                   for kind, values in timed.items() if values}
                    self._write_report(report, path)
        finally:
            signal.signal(signal.SIGINT, prior_handler)
        report["state"] = "paused" if pending else ("completed_with_gaps" if any(
            row["state"] != "completed" for row in report["sources"]) else "completed")
        report.update(finishedAt=timezone.now().isoformat(), activeDocuments=0,
                      states=dict(Counter(row["state"] for row in report["sources"])))
        self._write_report(report, path)
        self.stdout.write(f"{report['state']}: {report['states']}")

    @staticmethod
    def _aliases(canonical, aliases):
        if canonical["state"] in {"queued", "processing"}:
            return
        for row in aliases.get(canonical["path"], []):
            row.update({key: value for key, value in canonical.items()
                        if key not in {"path", "sha256", "mediaType", "duplicateOf"}})