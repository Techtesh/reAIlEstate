import hashlib
import json
import time
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from filelock import FileLock, Timeout

from platform_api.artifact_storage import move_document, storage
from platform_api.floor_plan_vision import get_floor_plan_vision_provider, resolve_vision_selection
from platform_api.models import AuditEvent, EvidenceDocument, ExtractionJob, Organization, OrganizationMembership, OutboxMessage, Project, ProjectMerge
from platform_api.sample_corpus import categorize_samples, prioritize_project_sources, skipped_samples
from platform_api.sample_reports import build_group_report
from platform_api.workers import process_document


class Command(BaseCommand):
    help = "Import all supported sources per domain/project folder, excluding .ignore and videos."

    def add_arguments(self, parser):
        parser.add_argument("source", type=Path, help="Root Samples directory.")
        parser.add_argument("--import", dest="import_samples", action="store_true")
        parser.add_argument("--process", action="store_true")
        parser.add_argument("--reprocess", action="store_true", help="Re-extract existing evidence without deleting prior runs.")
        parser.add_argument("--resume", action="store_true", help="Resume the same report, retrying incomplete sources.")
        parser.add_argument("--resume-status", action="append", default=[],
                choices=["pending", "processing", "failed", "awaiting_visual_analysis", "extraction_failed"],
                help="When resuming, process only rows/documents in this status; repeat for multiple statuses.")
        parser.add_argument("--retry-failed", action="store_true",
                help="Resume failed, extraction_failed, and awaiting_visual_analysis sources only.")
        parser.add_argument("--test-scanner", action="store_true", help="Explicit local-only malware scanning bypass for trusted samples.")
        parser.add_argument("--username", help="Existing local uploader (required with --import).")
        parser.add_argument("--organization", default="Sample corpus")
        parser.add_argument("--vision-profile-id", help="Configured vision profile for this run.")
        parser.add_argument("--project-key", action="append", default=[],
                    help="Optional folder project key to process; repeat for multiple projects.")
        parser.add_argument("--report", type=Path, help="Report filename or absolute path within output/reports.")

    def handle(self, *args, **options):
        lock_root = Path(settings.WORKSPACE_DATA_ROOT) / "output" / "reports" / ".locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        try:
            with FileLock(str(lock_root / "corpus-refresh.lock"), timeout=0):
                return self._handle(*args, **options)
        except Timeout:
            raise CommandError("Another corpus refresh is already running.") from None

    def _handle(self, *args, **options):
        source = options["source"].expanduser().resolve()
        if not source.is_dir():
            raise CommandError(f"Sample directory does not exist: {source}")
        if (options["process"] or options["reprocess"]) and not options["import_samples"]:
            raise CommandError("--process and --reprocess require --import.")
        if options["reprocess"] and not options["process"]:
            raise CommandError("--reprocess requires --process.")
        if options["test_scanner"] and not options["process"]:
            raise CommandError("--test-scanner requires --process.")
        if options["test_scanner"] and (not settings.DEBUG or settings.ENVIRONMENT not in {"local", "development"}):
            raise CommandError("--test-scanner is only available in local/development with DEBUG enabled.")
        if options["import_samples"] and not options["username"]:
            raise CommandError("--username is required with --import.")

        reports_root = Path(settings.WORKSPACE_DATA_ROOT) / "output" / "reports"
        report_path = options["report"] or Path("sample-corpus.json")
        if not report_path.is_absolute():
            report_path = reports_root / report_path
        report_path = report_path.expanduser().resolve()
        if not report_path.is_relative_to(reports_root.resolve()):
            raise CommandError("Reports must be stored within WORKSPACE_DATA_ROOT/output/reports.")
        samples, ignored = categorize_samples(source, settings.MAX_UPLOAD_BYTES)
        requested_keys = {key.casefold().strip() for key in options["project_key"] if key.strip()}
        if requested_keys:
            samples = [sample for sample in samples if sample.project_key in requested_keys]
            missing = requested_keys - {sample.project_key for sample in samples}
            if missing:
                raise CommandError("Unknown project key(s): " + ", ".join(sorted(missing)))
        report = self._report(samples, ignored, source)
        selection = resolve_vision_selection(options["vision_profile_id"]) if options["process"] else None
        report.update({"sourceRoot": str(source), "startedAt": timezone.now().isoformat(),
                       "organization": options["organization"], "visionSelection": selection,
                       "scanner": "local_test_bypass" if options["test_scanner"] else settings.DOCUMENT_SCANNER,
                       "reprocess": options["reprocess"], "resumeStatuses": sorted(set(options["resume_status"])),
                       "state": "catalogued", "sources": [], "projects": []})
        resume_statuses = set(options["resume_status"])
        if options["retry_failed"]:
            resume_statuses.update({"failed", "extraction_failed", "awaiting_visual_analysis"})
        report["resumeStatuses"] = sorted(resume_statuses)
        previous = {}
        if options["resume"]:
            if not report_path.exists():
                raise CommandError("--resume requires an existing report.")
            old = json.loads(report_path.read_text(encoding="utf-8"))
            for key in ("sourceRoot", "organization", "visionSelection", "scanner", "reprocess"):
                if old.get(key) != report[key]:
                    raise CommandError(f"Cannot resume: {key} has changed.")
            previous = {item["path"]: item for item in old.get("sources", [])}
            report["startedAt"] = old["startedAt"]
        self._write_report(report, report_path)
        self.stdout.write(f"Catalogued {len(samples)} supported sources; {len(ignored)} ignored files. Report: {report_path}")
        if not options["import_samples"]:
            return

        uploader = get_user_model().objects.filter(username=options["username"]).first()
        if uploader is None:
            raise CommandError(f"No user exists with username '{options['username']}'.")
        if OutboxMessage.objects.filter(published_at__isnull=True, dead_lettered_at__isnull=True,
                                        event_type="document.processing.requested").exists():
            raise CommandError("Pending document work exists; finish it before synchronous corpus processing.")
        organization, _ = Organization.objects.get_or_create(legal_name=options["organization"])
        OrganizationMembership.objects.get_or_create(organization=organization, user=uploader,
                                                     defaults={"role": "owner", "status": "active"})
        groups = defaultdict(list)
        for sample in samples:
            groups[sample.project_key].append(sample)
        report["state"] = "processing" if options["process"] else "importing"
        scanner = _CleanTestScanner() if options["test_scanner"] else None
        provider = get_floor_plan_vision_provider(selection) if selection else None
        try:
            for key, unordered_group in groups.items():
                group = prioritize_project_sources(unordered_group)
                project = self._project(organization, key, group)
                group_rows = []
                by_digest = {}
                for sample in group:
                    relative = sample.path.relative_to(source).as_posix()
                    row = {"path": relative, "sha256": sample.sha256, "mediaType": sample.media_type,
                           "category": sample.category, "sourceRole": sample.source_role,
                           "processingPriority": sample.processing_priority,
                           "projectId": str(project.id), "projectKey": key, "state": "pending"}
                    group_rows.append(row)
                    report["sources"].append(row)
                    old_row = previous.get(relative, {})
                    if not sample.eligible:
                        row.update(state="skipped", errorCode="MAX_UPLOAD_BYTES_EXCEEDED")
                    elif sample.sha256 in by_digest:
                        canonical = by_digest[sample.sha256]
                        row.update({k: v for k, v in canonical.items() if k not in {"path", "sha256", "mediaType"}})
                        row["duplicateOf"] = canonical["path"]
                    elif (old_row.get("state") == "completed" and old_row.get("sha256") == sample.sha256
                          and old_row.get("projectId") == str(project.id)
                          and project.documents.filter(id=old_row.get("documentId"), sha256=sample.sha256,
                              ingestion_runs__id=old_row.get("ingestionRunId"), ingestion_runs__status="completed").exists()):
                        row.update(old_row)
                        row["resumed"] = True
                    else:
                        try:
                            document, created = self._stage(project, sample, uploader)
                            row.update(documentId=str(document.id), imported=created)
                            if options["process"]:
                                should_process = created or options["reprocess"] or document.status in {"quarantined", "extraction_failed"}
                                if resume_statuses:
                                    should_process = old_row.get("state") in resume_statuses or document.status in resume_statuses
                                if should_process:
                                    self._prepare_processing(document, uploader, relative, report["scanner"], selection)
                                    row["state"] = "processing"
                                    self._write_report(report, report_path)
                                    document = process_document(document.id, scanner=scanner, extractor=provider,
                                                                vision_selection=selection)
                                self._outcome(row, document)
                            else:
                                row.update(state="staged" if created else "existing", documentStatus=document.status)
                        except Exception as exc:
                            # Provider messages can contain credentials/URLs. Only persist safe error codes.
                            row.update(state="failed", errorCode=type(exc).__name__)
                    by_digest.setdefault(sample.sha256, row)
                    self.stdout.write(f"[{len(report['sources'])}/{len(samples)}] {row['state']}: {relative}")
                    self._write_report(report, report_path)
                group_report = build_group_report(project, key, group_rows)
                group_filename = hashlib.sha256(key.encode()).hexdigest()[:16] + ".json"
                group_path = report_path.parent / (report_path.stem + "-projects") / group_filename
                self._write_report(group_report, group_path)
                report["projects"].append({"projectKey": key, "projectId": str(project.id),
                    "name": group[0].project_name, "sourceCount": len(group_rows),
                    "states": dict(Counter(item["state"] for item in group_rows)),
                    "report": group_path.relative_to(reports_root).as_posix()})
                self._write_report(report, report_path)
        finally:
            if hasattr(provider, "close"):
                provider.close()
        report["states"] = dict(Counter(item["state"] for item in report["sources"]))
        report["state"] = "completed_with_gaps" if any(item["state"] in {"failed", "awaiting_visual_analysis", "skipped"}
                                                       for item in report["sources"]) else "completed"
        report["finishedAt"] = timezone.now().isoformat()
        self._write_report(report, report_path)
        self.stdout.write(self.style.SUCCESS(f"{report['state']}: {report['states']}. Reports: {report_path}"))

    @staticmethod
    def _project(organization, key, group):
        sample = group[0]
        projects = Project.objects.filter(organization=organization)
        project = projects.filter(address__sampleCorpusKey=key).first()
        if project is None:
            matches = list(projects.filter(submitted_name__iexact=sample.project_name, locality__iexact=sample.locality)[:2])
            if len(matches) == 1 and not matches[0].address.get("sampleCorpusKey"):
                project = matches[0]
        if project is None:
            # Reuse a legacy filename-named project only when shared evidence identifies one unclaimed record.
            matches = list(projects.filter(locality__iexact=sample.locality,
                documents__sha256__in=[item.sha256 for item in group]).distinct())
            if len(matches) == 1 and not matches[0].address.get("sampleCorpusKey"):
                project = matches[0]
        if project is None:
            project = Project.objects.create(organization=organization, submitted_name=sample.project_name,
                                             locality=sample.locality, status="ingestion", address={})
        # RERA IDs remain source claims, not an identity key capable of merging unrelated folders/tenants.
        address = dict(project.address)
        address.update(sampleCorpusKey=key, sampleCorpusDirectory=sample.project_directory,
                       sampleCorpusName=sample.project_name, source="sample_corpus")
        project.address = address
        project.save(update_fields=["address", "updated_at"])
        group_hashes = {item.sha256 for item in group}
        legacy_projects = projects.filter(
            locality__iexact=sample.locality, documents__sha256__in=group_hashes,
        ).exclude(id=project.id).distinct()
        for legacy in legacy_projects:
            if legacy.address.get("sampleCorpusKey") or legacy.address.get("mergedIntoProjectId"):
                continue
            legacy_hashes = set(legacy.documents.values_list("sha256", flat=True))
            if legacy_hashes and legacy_hashes <= group_hashes:
                legacy.address = dict(legacy.address) | {
                    "mergedIntoProjectId": str(project.id), "mergedIntoProjectKey": key,
                    "mergeReason": "sample_corpus_folder_identity",
                }
                legacy.status = "merged"
                legacy.save(update_fields=["address", "status", "updated_at"])
                ProjectMerge.objects.update_or_create(
                    source_project=legacy,
                    defaults={"target_project": project, "state": "applied",
                              "reason_code": "SAMPLE_CORPUS_FOLDER_IDENTITY", "applied_at": timezone.now()},
                )
        return project

    @staticmethod
    def _stage(project, sample, uploader):
        document = project.documents.filter(sha256=sample.sha256).first()
        if document:
            return document, False
        object_key = f"user/media/todo/{project.id}/{sample.sha256}{sample.path.suffix.casefold()}"
        with sample.path.open("rb") as source_file:
            stored_key = storage.save(object_key, File(source_file))
        try:
            return EvidenceDocument.objects.create(project=project, object_key=stored_key,
                original_name=sample.path.name, declared_media_type=sample.media_type,
                byte_length=sample.byte_length, sha256=sample.sha256, status="quarantined",
                uploaded_by=uploader, retention_until=timezone.now() + timedelta(days=30)), True
        except Exception:
            storage.delete(stored_key)
            raise

    @staticmethod
    @transaction.atomic
    def _prepare_processing(document, uploader, source_path, scanner, selection):
        if document.status not in {"quarantined", "extraction_failed", "needs_review", "approved"}:
            raise ValueError("NOT_REPROCESSABLE")
        if document.object_key.startswith("user/media/completed/"):
            move_document(document, "todo")
        document.status = "quarantined" if document.scan_status != "clean" else "extraction_failed"
        document.error_category = ""
        document.save(update_fields=["status", "error_category", "updated_at"])
        ExtractionJob.objects.create(document=document, extractor_version="sample-corpus-2.0", status="queued")
        AuditEvent.objects.create(actor=uploader, action="document.reprocessing_requested",
            entity_type="EvidenceDocument", entity_id=document.id, correlation_id=uuid4(),
            reason_code="SAMPLE_CORPUS_REFRESH", metadata={"sourcePath": source_path,
                "scanner": scanner, "visionSelection": selection, "extractorVersion": "sample-corpus-2.0"})

    @staticmethod
    def _outcome(row, document):
        row.update(documentStatus=document.status, scanStatus=document.scan_status, errorCode=document.error_category)
        run = document.ingestion_runs.order_by("-created_at").first()
        if run:
            row.update(ingestionRunId=str(run.id), processingStatus=run.summary.get("processingStatus"),
                       outputs=run.summary.get("outputs", {}))
        if document.status not in {"needs_review", "approved"} or not run or run.status == "failed":
            row["state"] = "failed"
        elif run.status == "needs_provider" or run.summary.get("processingStatus") == "awaiting_visual_analysis":
            row["state"] = "awaiting_visual_analysis"
        else:
            row["state"] = "completed"

    @staticmethod
    def _report(samples, ignored, source):
        pdfs = [item for item in samples if item.media_type == "application/pdf"]
        return {"schemaVersion": "sample-corpus-2.0", "documentCount": len(samples), "pdfCount": len(pdfs),
            "eligibleDocumentCount": sum(item.eligible and not item.duplicate_of for item in samples),
            "eligiblePdfCount": sum(item.eligible and not item.duplicate_of for item in pdfs),
            "projectCount": len({item.project_key for item in samples}), "ignoredFileCount": len(ignored),
            "localities": dict(Counter(item.locality for item in samples)),
            "categories": dict(Counter(item.category for item in samples)),
            "mediaTypes": dict(Counter(item.media_type for item in samples)),
            "oversized": [item.path.relative_to(source).as_posix() for item in samples if not item.eligible],
            "duplicates": [{"path": item.path.relative_to(source).as_posix(),
                            "duplicateOf": item.duplicate_of.relative_to(source).as_posix()}
                           for item in samples if item.duplicate_of],
            "skipped": skipped_samples(source)}

    @staticmethod
    def _write_report(report, report_path):
        report_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = report_path.with_suffix(report_path.suffix + ".tmp")
        temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        for attempt in range(10):
            try:
                temporary.replace(report_path)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.1 * (attempt + 1))


class _CleanTestScanner:
    """Explicit local-only bypass. This does not perform malware detection."""

    def scan(self, file_object):
        file_object.seek(0)
        return "clean"