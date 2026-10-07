"""Reclassify existing script images; never replace script plans or original documents."""

import getpass
import hashlib
import json
import os
import time
from pathlib import Path
from uuid import uuid4

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.test.utils import override_settings
from django.utils import timezone

from realestate_common.reports import write_json_report

from platform_api.artifact_storage import storage
from platform_api.floor_plan_agent import FloorPlanAgent
from platform_api.floor_plan_vision import get_floor_plan_vision_provider, resolve_vision_selection
from platform_api.integrations import ProviderUnavailable
from platform_api.vision_errors import VisionRequestError
from platform_api.models import AgentWorkflowRun, AuditEvent, FloorPlanAsset, IngestionRun


class Command(BaseCommand):
    help = "Classify script candidate images with AI and create gen_with_ai_needs_review records. No originals/script results are changed."

    def add_arguments(self, parser):
        parser.add_argument("--report", required=True, help="Local JSON progress report; existing report requires --resume.")
        parser.add_argument("--endpoint", required=True)
        parser.add_argument("--model", required=True)
        parser.add_argument("--prompt-api-key", action="store_true")
        parser.add_argument("--use-entra-id", action="store_true",
                    help="Authenticate Azure Foundry with DefaultAzureCredential instead of an API key.")
        parser.add_argument("--resume", action="store_true")
        parser.add_argument("--project-id")
        parser.add_argument("--retry-attempts", type=int, default=3, help="Extra per-image attempts for transient provider failures (0-5).")
        parser.add_argument("--delay-seconds", type=float, default=2.0, help="Delay between model calls (0-60 seconds).")

    save_report = staticmethod(write_json_report)

    def handle(self, *args, **options):
        if not 0 <= options["retry_attempts"] <= 5 or not 0 <= options["delay_seconds"] <= 60:
            raise CommandError("Invalid retry/delay bounds.")
        path = Path(options["report"]).resolve()
        if options["prompt_api_key"] and options["use_entra_id"]:
            raise CommandError("Choose --prompt-api-key or --use-entra-id, not both.")
        profile = {"provider": "azure_foundry", "model": options["model"], "base_url": options["endpoint"],
                   "api_key_env": None if options["use_entra_id"] else "FLOOR_PLAN_VISION_API_KEY"}
        with override_settings(FLOOR_PLAN_EXTRACTION_MODE="ai", FLOOR_PLAN_VISION_PROFILES={"ai-classification": profile},
                               FLOOR_PLAN_DEFAULT_VISION_PROFILE="ai-classification"):
            selection = resolve_vision_selection()
            if path.exists():
                if not options["resume"]:
                    raise CommandError("Report exists; use --resume to avoid duplicate accepted results.")
                report = json.loads(path.read_text(encoding="utf-8"))
                if report.get("selection") != selection:
                    raise CommandError("Resume must use the original provider/model configuration.")
            else:
                if options["resume"]:
                    raise CommandError("No report to resume.")
                query = FloorPlanAsset.objects.filter(status="ready", document__scan_status="clean",
                    workflow_run__result__visionProvider="rules", floor_plans__isnull=False).distinct()
                if options["project_id"]:
                    query = query.filter(project_id=options["project_id"])
                asset_ids = [str(pk) for pk in query.order_by("project_id", "document_id", "page_number", "id").values_list("id", flat=True)]
                report = {"batchId": str(uuid4()), "startedAt": timezone.now().isoformat(), "status": "awaiting_credentials",
                          "selection": selection, "reviewState": "gen_with_ai_needs_review", "assetIds": asset_ids,
                          "results": [], "note": "Existing saved PNGs only. Original PDFs and script results are unchanged."}
                path.parent.mkdir(parents=True, exist_ok=True)
                self.save_report(path, report)
            self.stdout.write(f"Candidate images: {len(report['assetIds'])}. Results will be separate AI records.")
            self.stdout.flush()
            old_key = os.environ.get("FLOOR_PLAN_VISION_API_KEY")
            lock_path = path.with_suffix(".lock")
            try:
                lock = lock_path.open("x")
            except FileExistsError as exc:
                raise CommandError("This report already has a running batch lock.") from exc
            lock.close()
            try:
                if options["prompt_api_key"]:
                    self.stdout.write("Enter a rotated Azure API key in this terminal; it is not saved to files.")
                    key = getpass.getpass("Azure API key (hidden): ")
                    if not key.strip():
                        raise CommandError("No API key provided; no inference started.")
                    os.environ["FLOOR_PLAN_VISION_API_KEY"] = key
                    del key
                if not options["use_entra_id"] and not os.environ.get("FLOOR_PLAN_VISION_API_KEY"):
                    raise CommandError("Set FLOOR_PLAN_VISION_API_KEY or use --prompt-api-key.")
                provider = get_floor_plan_vision_provider(selection)
                agent = FloorPlanAgent(provider)
                completed = {item["assetId"] for item in report["results"] if item["status"] in {"accepted", "not_plan", "uncertain"}}
                report["status"] = "running"
                self.save_report(path, report)
                for index, asset_id in enumerate(report["assetIds"], 1):
                    if asset_id in completed:
                        continue
                    # Checkpoint in DB prevents duplicate inference after report-write interruption.
                    previous = AgentWorkflowRun.objects.filter(workflow_type="ai_plan_reclassification",
                        result__batchId=report["batchId"], result__sourceAssetId=asset_id, status="completed").first()
                    if previous:
                        outcome = previous.result["outcome"]
                    else:
                        asset = FloorPlanAsset.objects.select_related("document", "project").get(id=asset_id)
                        self.stdout.write(f"[{index}/{len(report['assetIds'])}] {asset.project.submitted_name}, page {asset.page_number}")
                        self.stdout.flush()
                        try:
                            if asset.document.scan_status != "clean":
                                raise ValueError("SOURCE_NO_LONGER_CLEAN")
                            with storage.open(asset.object_key) as image:
                                data = image.read(min(asset.byte_length, 20 * 1024 * 1024) + 1)
                            if len(data) != asset.byte_length or hashlib.sha256(data).hexdigest() != asset.sha256:
                                raise ValueError("IMAGE_INTEGRITY_MISMATCH")
                            for attempt in range(options["retry_attempts"] + 1):
                                try:
                                    result = provider.analyze_classified_plan(data, asset.media_type, agent._vision_schema())
                                    break
                                except VisionRequestError as exc:
                                    if not exc.retryable or attempt == options["retry_attempts"]:
                                        raise
                                    delay = min(300, max(exc.retry_after or 0, 15 * (2 ** attempt)))
                                    self.stdout.write(f"  {exc.code}; retrying in {delay:g}s ({attempt + 1}/{options['retry_attempts']}).")
                                    self.stdout.flush()
                                    time.sleep(delay)
                            outcome = self.persist_result(report, asset, data, result, agent)
                        except ProviderUnavailable as exc:
                            report["status"] = "paused_provider_unavailable"
                            report["lastError"] = {"assetId": asset_id, "pageNumber": asset.page_number,
                                "code": exc.code if isinstance(exc, VisionRequestError) else "VISION_PROVIDER_UNAVAILABLE",
                                "httpStatus": exc.status_code if isinstance(exc, VisionRequestError) else None,
                                "retryable": exc.retryable if isinstance(exc, VisionRequestError) else False,
                                "occurredAt": timezone.now().isoformat()}
                            self.save_report(path, report)
                            raise CommandError(f"{report['lastError']['code']} (HTTP {report['lastError']['httpStatus']}). Batch paused; resume the same report after resolving the issue. Completed results are preserved.") from None
                        except Exception as exc:
                            outcome = {"assetId": asset_id, "status": "failed", "errorCode": type(exc).__name__}
                    report["results"] = [item for item in report["results"] if item["assetId"] != asset_id] + [outcome]
                    self.save_report(path, report)
                    time.sleep(options["delay_seconds"])
                report["status"] = "completed" if not any(item["status"] == "failed" for item in report["results"]) else "completed_with_failures"
                report["completedAt"] = timezone.now().isoformat()
                report.pop("lastError", None)
                report["counts"] = {state: sum(item["status"] == state for item in report["results"])
                                    for state in ("accepted", "not_plan", "uncertain", "failed")}
                self.save_report(path, report)
                self.stdout.write(json.dumps(report["counts"]))
            finally:
                if "provider" in locals() and hasattr(provider, "close"):
                    provider.close()
                if old_key is None:
                    os.environ.pop("FLOOR_PLAN_VISION_API_KEY", None)
                else:
                    os.environ["FLOOR_PLAN_VISION_API_KEY"] = old_key
                lock_path.unlink(missing_ok=True)

    def persist_result(self, report, source, data, result, agent):
        key = None
        try:
            with transaction.atomic():
                run = IngestionRun.objects.create(project=source.project, document=source.document,
                    pipeline_version="ai-plan-reclassification-1.0", extraction_provider=agent.vision_provider.code,
                    status="completed", started_at=timezone.now(), completed_at=timezone.now(),
                    summary={"batchId": report["batchId"], "sourceAssetId": str(source.id),
                             "scope": "saved_image_classification_only", "selection": report["selection"]})
                workflow = AgentWorkflowRun.objects.create(project=source.project, document=source.document,
                    ingestion_run=run, workflow_type="ai_plan_reclassification", workflow_version=agent.version,
                    status="running", started_at=timezone.now())
                candidates = agent._reconcile(agent._validate(agent._vision_candidates(source.page_number, result)))
                plans = []
                if candidates:
                    scopes = {item.plan_scope for item in candidates}
                    category = "layouts" if scopes == {"flat"} else "floor-plans" if scopes == {"floor"} else "unclassified"
                    key = storage.save(f"output/media/images/{source.project_id}/{category}/{workflow.id}/{uuid4().hex}.png", ContentFile(data))
                    new_asset = FloorPlanAsset.objects.create(project=source.project, document=source.document,
                        workflow_run=workflow, page_number=source.page_number, asset_type=source.asset_type,
                        object_key=key, media_type=source.media_type, byte_length=len(data), sha256=source.sha256,
                        pixel_width=source.pixel_width, pixel_height=source.pixel_height,
                        source_bounds=source.source_bounds | {"category": category, "sourceAssetId": str(source.id),
                            "aiClassification": {"pageType": result["pageType"], "visualEvidence": result["visualEvidence"]}},
                        detector_version="ai-plan-classifier-1.0", detection_confidence=max(item.confidence for item in candidates))
                    from dataclasses import replace
                    candidates = [replace(item, asset=new_asset) for item in candidates]
                    plans = agent._persist(workflow, candidates)
                    agent._review_gate(workflow, plans)
                state = "accepted" if plans else "uncertain" if result["pageType"] in {"uncertain", "floor_plan", "flat_layout", "mixed_plan"} else "not_plan"
                outcome = {"assetId": str(source.id), "projectId": str(source.project_id), "projectName": source.project.submitted_name,
                           "pageNumber": source.page_number, "status": state, "pageType": result["pageType"],
                           "visualEvidence": result["visualEvidence"], "planIds": [str(plan.id) for plan in plans],
                           "runId": str(run.id), "workflowRunId": str(workflow.id)}
                workflow.result = {"batchId": report["batchId"], "sourceAssetId": str(source.id), "outcome": outcome,
                                   **agent._provider_metadata()}
                workflow.status = "completed"
                workflow.completed_at = timezone.now()
                workflow.save()
                AuditEvent.objects.create(actor=None, action="asset.ai_reclassified", entity_type="FloorPlanAsset",
                    entity_id=source.id, correlation_id=report["batchId"], metadata={"pageType": result["pageType"], "planIds": outcome["planIds"]})
                return outcome
        except Exception:
            if key:
                storage.delete(key)
            raise