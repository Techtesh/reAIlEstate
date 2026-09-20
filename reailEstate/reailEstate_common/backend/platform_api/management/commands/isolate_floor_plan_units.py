import getpass
import json
import os
from contextlib import nullcontext
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.test.utils import override_settings
from django.utils import timezone

from platform_api.floor_plan_segmentation_service import FlatSeparationFailed, separate_floor_plan_units


class Command(BaseCommand):
    help = "Create per-flat crops from one full-floor FloorPlan in local/development environments."

    def add_arguments(self, parser):
        parser.add_argument("floor_plan_id")
        parser.add_argument("--mode", choices=["script", "ai", "both"], default="script")
        parser.add_argument("--expected-count", type=int, help="Optional quality gate; never forces invented boxes.")
        parser.add_argument("--vision-profile-id", help="Configured server-side AI vision profile.")
        parser.add_argument("--endpoint", help="Local/development-only Azure/OpenAI-compatible profile override.")
        parser.add_argument("--model", help="Model for --endpoint.")
        parser.add_argument("--prompt-api-key", action="store_true")
        parser.add_argument("--report", required=True, help="New JSON report path.")
        parser.add_argument("--supersede-source", action="store_true",
                            help="After every requested method succeeds, mark the full-floor record as superseded.")

    @staticmethod
    def save(path, report):
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary.replace(path)

    def handle(self, *args, **options):
        if not settings.DEBUG or str(getattr(settings, "ENVIRONMENT", "")).casefold() not in {"local", "development"}:
            raise CommandError(
                "This ad hoc command is restricted to local/development. Use the authenticated admin portal or API elsewhere."
            )
        if options["expected_count"] is not None and not 1 <= options["expected_count"] <= 40:
            raise CommandError("expected-count must be between 1 and 40")
        if bool(options["endpoint"]) != bool(options["model"]):
            raise CommandError("--endpoint and --model must be supplied together.")
        if options["vision_profile_id"] and options["endpoint"]:
            raise CommandError("Choose --vision-profile-id or the local --endpoint/--model override, not both.")
        if options["prompt_api_key"] and not options["endpoint"]:
            raise CommandError("--prompt-api-key is supported only with the local --endpoint/--model override.")
        if options["mode"] == "script" and (options["vision_profile_id"] or options["endpoint"] or options["prompt_api_key"]):
            raise CommandError("AI options cannot be used with script-only mode.")

        report_path = Path(options["report"]).resolve()
        if report_path.exists():
            raise CommandError("Report already exists; choose a new path.")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "startedAt": timezone.now().isoformat(), "status": "running",
            "requestedMode": options["mode"], "expectedCount": options["expected_count"],
            "note": "Generated child crops are review proposals; source evidence is preserved by default.",
        }
        self.save(report_path, report)

        old_key = os.environ.get("FLOOR_PLAN_VISION_API_KEY")
        try:
            if options["prompt_api_key"]:
                self.stdout.write("Enter a rotated Azure API key; hidden input is not stored.")
                key = getpass.getpass("Azure API key: ")
                if not key.strip():
                    raise CommandError("No API key provided.")
                os.environ["FLOOR_PLAN_VISION_API_KEY"] = key
                del key

            profile_id = options["vision_profile_id"]
            profile_context = nullcontext()
            if options["endpoint"]:
                profile_id = "cli-flat-separation"
                profile = {
                    "label": "CLI flat separation", "provider": "azure_foundry",
                    "model": options["model"], "base_url": options["endpoint"],
                    "api_key_env": "FLOOR_PLAN_VISION_API_KEY" if options["prompt_api_key"] else None,
                }
                profile_context = override_settings(
                    FLOOR_PLAN_VISION_PROFILES={profile_id: profile},
                    FLOOR_PLAN_DEFAULT_VISION_PROFILE=profile_id,
                )

            with profile_context:
                result = separate_floor_plan_units(
                    options["floor_plan_id"], mode=options["mode"],
                    expected_count=options["expected_count"], vision_profile_id=profile_id,
                    supersede_source=options["supersede_source"], actor=None,
                )
            report.update(result)
            report["completedAt"] = timezone.now().isoformat()
            self.save(report_path, report)
            self.stdout.write(json.dumps({
                method: details.get("unitCount", details.get("errorCode"))
                for method, details in result["methods"].items()
            }))
            self.stdout.write(f"Report: {report_path}")
        except FlatSeparationFailed as exc:
            report.update(exc.result)
            report["status"] = "failed"
            report["completedAt"] = timezone.now().isoformat()
            self.save(report_path, report)
            raise CommandError("No requested flat-separation method completed; inspect the report.") from None
        except Exception as exc:
            report["status"] = "failed"
            report["errorCode"] = str(exc)[:100] if isinstance(exc, ValueError) else type(exc).__name__
            report["completedAt"] = timezone.now().isoformat()
            self.save(report_path, report)
            raise
        finally:
            if old_key is None:
                os.environ.pop("FLOOR_PLAN_VISION_API_KEY", None)
            else:
                os.environ["FLOOR_PLAN_VISION_API_KEY"] = old_key
