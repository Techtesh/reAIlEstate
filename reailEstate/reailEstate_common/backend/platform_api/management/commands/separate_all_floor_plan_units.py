import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from realestate_common.reports import write_json_report

from platform_api.floor_plan_segmentation_service import FlatSeparationFailed, separate_floor_plan_units
from platform_api.models import AgentWorkflowRun, FloorPlan, FloorPlanAsset


class Command(BaseCommand):
    help = "Create missing script flat crops for every unique clean full-floor source in local/development."

    def add_arguments(self, parser):
        parser.add_argument("--report", required=True, help="New JSON checkpoint report path.")

    save = staticmethod(write_json_report)

    def handle(self, *args, **options):
        if not settings.DEBUG or str(getattr(settings, "ENVIRONMENT", "")).casefold() not in {"local", "development"}:
            raise CommandError("This bulk command is restricted to local/development environments.")
        report_path = Path(options["report"]).resolve()
        if report_path.exists():
            raise CommandError("Report already exists; choose a new path.")
        report_path.parent.mkdir(parents=True, exist_ok=True)

        existing = {
            str(value) for value in AgentWorkflowRun.objects.filter(
                workflow_type="unit_segmentation", result__segmentationMethod="script",
            ).values_list("result__sourceAssetId", flat=True)
        }
        rule_floor_plans = FloorPlan.objects.filter(
            plan_scope="floor", asset__isnull=False, asset__status="ready", document__scan_status="clean",
        ).exclude(asset__asset_type="unit_crop").exclude(
            asset__source_bounds__has_key="sourceAssetId"
        ).select_related("project", "asset").order_by(
            "project_id", "asset_id", "id"
        )
        ai_source_ids = FloorPlanAsset.objects.filter(
            status="ready", document__scan_status="clean",
            source_bounds__aiClassification__pageType__in=["floor_plan", "mixed_plan"],
        ).values_list("source_bounds__sourceAssetId", flat=True)
        ai_confirmed_plans = FloorPlan.objects.filter(
            asset_id__in=ai_source_ids, asset__status="ready", document__scan_status="clean",
        ).select_related("project", "asset").order_by("project_id", "asset_id", "id")
        seen = set()
        candidates = []
        for plan in list(rule_floor_plans) + list(ai_confirmed_plans):
            asset_id = str(plan.asset_id)
            if asset_id not in seen and asset_id not in existing:
                seen.add(asset_id)
                candidates.append(plan)

        report = {
            "startedAt": timezone.now().isoformat(), "status": "running", "mode": "script",
            "sourceCount": len(candidates), "sourcePreserved": True, "results": [],
        }
        self.save(report_path, report)
        for index, plan in enumerate(candidates, 1):
            self.stdout.write(f"[{index}/{len(candidates)}] {plan.project.submitted_name}, page {plan.page_number}")
            self.stdout.flush()
            item = {
                "projectId": str(plan.project_id), "projectName": plan.project.submitted_name,
                "floorPlanId": str(plan.id), "sourceAssetId": str(plan.asset_id), "pageNumber": plan.page_number,
            }
            try:
                result = separate_floor_plan_units(plan.id, mode="script", supersede_source=False)
                item.update({
                    "status": result["status"], "unitCount": result["methods"]["script"]["unitCount"],
                    "workflowRunId": result["methods"]["script"]["workflowRunId"],
                })
            except FlatSeparationFailed as exc:
                item.update({
                    "status": "failed",
                    "errorCode": exc.result["methods"].get("script", {}).get("errorCode", "FAILED"),
                })
            except Exception as exc:
                item.update({"status": "failed", "errorCode": str(exc)[:100]})
            report["results"].append(item)
            self.save(report_path, report)

        report["status"] = "completed"
        report["completedAt"] = timezone.now().isoformat()
        report["counts"] = {
            "completed": sum(item["status"] == "completed" for item in report["results"]),
            "failed": sum(item["status"] == "failed" for item in report["results"]),
            "layoutsCreated": sum(item.get("unitCount", 0) for item in report["results"]),
        }
        self.save(report_path, report)
        self.stdout.write(json.dumps(report["counts"], indent=2))
        self.stdout.write(f"Report: {report_path}")
