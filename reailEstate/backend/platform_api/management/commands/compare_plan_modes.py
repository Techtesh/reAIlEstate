"""Compare local rules and an existing vision profile without updating domain records."""

import base64
import getpass
import hashlib
import html
import json
import os
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pymupdf
from platform_api.artifact_storage import storage as default_storage
from django.core.management.base import BaseCommand, CommandError
from django.test.utils import override_settings
from django.utils import timezone

from platform_api.floor_plan_agent import FloorPlanAgent
from platform_api.floor_plan_extractor import FloorPlanImageExtractor
from platform_api.floor_plan_pages import FloorPlanPageSelector
from platform_api.floor_plan_rules import RuleBasedPlanProvider
from platform_api.floor_plan_vision import get_floor_plan_vision_provider, resolve_vision_selection
from platform_api.models import EvidenceDocument


def candidate_data(candidate):
    # Never serialize model instances, endpoints, credentials or provider internals.
    result = asdict(candidate)
    result.pop("asset", None)
    if result["carpet_area_sqft"] is not None:
        result["carpet_area_sqft"] = str(result["carpet_area_sqft"])
    return result


def save_report(directory, report):
    (directory / "comparison.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    sections = []
    for page in report["pages"]:
        encoded = base64.b64encode((directory / page["image"]).read_bytes()).decode("ascii")
        script = html.escape(json.dumps(page["script"], indent=2, ensure_ascii=False))
        ai = html.escape(json.dumps(page["ai"], indent=2, ensure_ascii=False))
        sections.append(
            f'<section><h2>Page {page["pageNumber"]}</h2>'
            f'<p>Selection signals: {html.escape(", ".join(page["reasons"]))}</p>'
            f'<img alt="Source page {page["pageNumber"]}" src="data:image/png;base64,{encoded}">'
            f'<div class="columns"><article><h3>Script · local rules</h3><pre>{script}</pre></article>'
            f'<article><h3>AI · {html.escape(report["model"] or "not requested")}</h3><pre>{ai}</pre></article></div></section>'
        )
    markup = (
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
        '<title>Plan extraction comparison</title><style>'
        'body{font:16px system-ui;background:#eff3f8;color:#172b45;margin:0;padding:32px;max-width:1400px;margin:auto}'
        'section{background:white;border-radius:16px;padding:24px;margin:24px 0;box-shadow:0 4px 20px #172b4510}'
        'img{max-width:100%;max-height:650px;display:block;margin:auto;border:1px solid #ccd6e0}'
        '.columns{display:grid;grid-template-columns:1fr 1fr;gap:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}'
        'article{min-width:0;background:#f6f8fc;border-radius:12px;padding:16px}@media(max-width:800px){.columns{grid-template-columns:1fr}}'
        '</style><h1>Script vs AI: ' + html.escape(report["project"]) + '</h1>'
        '<p>Same selected pages and PNG inputs. Unreviewed outputs, not verified facts. '
        'Script mode identifies page candidates; AI attempts structured visual extraction. '
        'This does not evaluate missed pages or prove accuracy. No project records were changed.</p>'
        + '<p>Status: ' + html.escape(report["status"]) + '</p>' + ''.join(sections) + '</html>'
    )
    (directory / "comparison.html").write_text(markup, encoding="utf-8")


class Command(BaseCommand):
    help = "Export script versus vision output for the same scanned-clean PDF pages without database writes."

    def add_arguments(self, parser):
        parser.add_argument("document_id")
        parser.add_argument("--limit", type=int, default=3)
        parser.add_argument("--output", required=True, help="New private local report directory.")
        parser.add_argument("--endpoint", help="Azure OpenAI v1 endpoint (not a project endpoint).")
        parser.add_argument("--model", help="Azure vision deployment name.")
        parser.add_argument("--prompt-api-key", action="store_true", help="Read key with masked terminal input, never from arguments.")
        parser.add_argument("--script-only", action="store_true")

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= 8:
            raise CommandError("limit must be between 1 and 8")
        if not options["script_only"] and not (options["endpoint"] and options["model"]):
            raise CommandError("AI comparison requires --endpoint and --model")
        document = EvidenceDocument.objects.select_related("project").get(pk=options["document_id"])
        if document.scan_status != "clean" or document.declared_media_type != "application/pdf":
            raise CommandError("Only an existing scanned-clean PDF can be compared.")
        directory = Path(options["output"]).resolve()
        if directory.exists():
            raise CommandError("Choose a new output directory; existing reports will not be overwritten.")
        with default_storage.open(document.object_key, "rb") as source:
            content = source.read(document.byte_length + 1)
        if len(content) != document.byte_length or hashlib.sha256(content).hexdigest() != document.sha256:
            raise CommandError("Source size/hash mismatch; rescan before comparing.")
        selected = FloorPlanPageSelector(maximum_pages=options["limit"]).select(content, "application/pdf")
        directory.mkdir(parents=True)
        agent = FloorPlanAgent(RuleBasedPlanProvider())
        report = {"project": document.project.submitted_name, "documentId": str(document.id),
                  "sourceSha256": document.sha256, "createdAt": timezone.now().isoformat(),
                  "model": options["model"], "status": "script_completed", "pages": []}
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            for chosen in selected:
                page = pdf[chosen.page_number - 1]
                score = min(1.0, chosen.score / 12.0)
                crop = FloorPlanImageExtractor()._render_pdf_crop(page, chosen.page_number, page.rect, score)
                image_name = f"page-{chosen.page_number}.png"
                (directory / image_name).write_bytes(crop.content)
                chunk = SimpleNamespace(page_number=chosen.page_number, content=page.get_text("text")[:100_000])
                context = {"chunk": chunk, "text": chunk.content, "score": score, "selectionReasons": list(chosen.reasons)}
                candidates = agent._validate(agent._identify_without_ai([context], []))
                report["pages"].append({"pageNumber": chosen.page_number, "image": image_name,
                    "imageSha256": hashlib.sha256(crop.content).hexdigest(), "reasons": list(chosen.reasons),
                    "script": [candidate_data(item) for item in candidates], "ai": {"status": "not_run"}})
        save_report(directory, report)
        self.stdout.write(f"Script report ready: {directory / 'comparison.html'}")
        if options["script_only"] or not selected:
            return
        key_name = "FLOOR_PLAN_VISION_API_KEY"
        previous_key = os.environ.get(key_name)
        try:
            if options["prompt_api_key"]:
                report["status"] = "awaiting_api_key"
                save_report(directory, report)
                self.stdout.write("Enter the replacement Azure API key in this terminal (hidden input).")
                key = getpass.getpass("Azure API key: ")
                if not key.strip():
                    raise CommandError("No API key entered. Script report retained; AI was not run.")
                os.environ[key_name] = key
                del key
            profile = {"provider": "azure_foundry", "model": options["model"],
                       "base_url": options["endpoint"], "api_key_env": key_name}
            with override_settings(FLOOR_PLAN_EXTRACTION_MODE="ai", FLOOR_PLAN_VISION_PROFILES={"comparison": profile},
                                   FLOOR_PLAN_DEFAULT_VISION_PROFILE="comparison"):
                provider = get_floor_plan_vision_provider(resolve_vision_selection())
                for item in report["pages"]:
                    self.stdout.write(f"AI extraction: page {item['pageNumber']}")
                    try:
                        result = provider.analyze((directory / item["image"]).read_bytes(), "image/png", agent._vision_schema())
                        item["ai"] = {"status": "completed", "result": result}
                    except Exception as exc:
                        item["ai"] = {"status": "failed", "errorCode": type(exc).__name__}
                    save_report(directory, report)
            report["status"] = "completed" if all(item["ai"]["status"] == "completed" for item in report["pages"]) else "partial_failure"
            save_report(directory, report)
            self.stdout.write(f"Comparison {report['status']}: {directory / 'comparison.html'}")
        finally:
            if previous_key is None:
                os.environ.pop(key_name, None)
            else:
                os.environ[key_name] = previous_key