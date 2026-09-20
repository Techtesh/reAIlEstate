import hashlib
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db import transaction
from .artifact_storage import storage as default_storage
from django.utils import timezone

from .floor_plan_extractor import FloorPlanImageExtractor
from .floor_plan_pages import FloorPlanPageSelector, SelectedFloorPlanPage
from .floor_plan_rules import RULE_VERSION, classify_plan_scope
from .integrations import ProviderUnavailable
from .models import AgentWorkflowRun, AgentWorkflowStep, DocumentLayoutPage, FactCandidate, FloorPlan, FloorPlanAsset, ReviewTask
from .rag import retrieve
from .ingestion_runtime import event
from .domain.room_dimensions import calculate_room_areas


FLOOR_PLAN_TERMS = re.compile(r"\b(floor\s*plan|unit\s*plan|flat\s*plan|apartment\s*plan|typical\s*plan|tower\s*plan|wing\s*plan|layout)\b", re.IGNORECASE)
CONFIGURATION_PATTERN = re.compile(r"\b([1-6](?:\.5)?\s*BHK|studio)\b", re.IGNORECASE)
AREA_PATTERN = re.compile(r"(?:carpet\s*area|rera\s*carpet)[^\d]{0,30}(\d{2,5}(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|square\s*feet)", re.IGNORECASE)
DIMENSION_PATTERN = re.compile(
    r"\b(living|dining|kitchen|bed(?:room)?(?:\s*\d+)?|master\s*bed(?:room)?|bath(?:room)?(?:\s*\d+)?|toilet|balcony)\b"
    r"[^\d]{0,25}(\d{1,2}(?:\.\d+)?)[\s'’-]*(?:ft|feet|')?\s*[x×]\s*(\d{1,2}(?:\.\d+)?)[\s'’-]*(?:ft|feet|')?",
    re.IGNORECASE,
)


class FloorPlanVisionProvider:
    code = "unavailable"

    def analyze(self, image_bytes, media_type, fixed_schema):
        raise ProviderUnavailable("A reviewed vision provider is required for image-only floor plans.")


@dataclass(frozen=True)
class FloorPlanCandidate:
    page_number: int | None
    plan_name: str
    configuration_type: str
    carpet_area_sqft: Decimal | None
    dimensions: list[dict]
    confidence: float
    evidence_text: str
    asset: FloorPlanAsset | None = None
    plan_scope: str = "unknown"
    orientation: str = ""
    features: dict | None = None


class FloorPlanAgent:
    """Bounded workflow: detect, extract, validate, reconcile, and request review."""

    version = "floor-plan-agent-1.5"

    def __init__(self, vision_provider=None):
        self._owns_vision_provider = vision_provider is None
        if vision_provider is None:
            from .floor_plan_vision import get_floor_plan_vision_provider
            vision_provider = get_floor_plan_vision_provider()
        self.vision_provider = vision_provider

    def run(self, ingestion_run, content=None, media_type=None):
        workflow = AgentWorkflowRun.objects.create(
            project=ingestion_run.project,
            document=ingestion_run.document,
            ingestion_run=ingestion_run,
            workflow_version=self.version,
            status="running",
            current_step="detect",
            started_at=timezone.now(),
        )
        try:
            contexts = self._step(workflow, 1, "select_pages", self._detect, ingestion_run, content, media_type)
            assets = self._step(workflow, 2, "crop_and_store", self._crop_and_store, workflow, contexts, content, media_type)
            candidates = self._step(workflow, 3, "extract", self._extract, ingestion_run, contexts, assets)
            validated = self._step(workflow, 4, "validate", self._validate, candidates)
            reconciled = self._step(workflow, 5, "reconcile", self._reconcile, validated)
            with transaction.atomic():
                floor_plans = self._persist(workflow, reconciled)
                self._step(workflow, 6, "review_gate", self._review_gate, workflow, floor_plans)
            workflow.status = "completed"
            workflow.current_step = "completed"
            workflow.completed_at = timezone.now()
            workflow.confidence = sum(item.confidence for item in floor_plans) / len(floor_plans) if floor_plans else 0
            workflow.result = {
                "floorPlanIds": [str(item.id) for item in floor_plans],
                "assetIds": [str(item.id) for item in assets],
                "detected": bool(contexts),
                **self._provider_metadata(),
                "requiresHumanReview": bool(floor_plans),
            }
            workflow.save(update_fields=["status", "current_step", "completed_at", "confidence", "result", "updated_at"])
            return workflow
        except ProviderUnavailable:
            # Provider failure is not a generated plan. Keep source images for inspection.
            floor_plans = []
            workflow.status = "needs_provider"
            workflow.error_code = ""
            workflow.completed_at = timezone.now()
            workflow.result = {
                "detected": True,
                "assetIds": [str(item) for item in workflow.floor_plan_assets.values_list("id", flat=True)],
                "floorPlanIds": [str(item.id) for item in floor_plans],
                **self._provider_metadata(),
                "requiresHumanReview": True,
                "reasonCode": "VISION_PROVIDER_UNAVAILABLE",
            }
            workflow.save(update_fields=["status", "error_code", "completed_at", "result", "updated_at"])
            ReviewTask.objects.get_or_create(
                project=workflow.project,
                task_type="floor_plan_vision",
                state="open",
                resolution={"workflowRunId": str(workflow.id)},
                defaults={"priority": 1},
            )
            return workflow
        except Exception as exc:
            # Preserve checkpointed source images on failure; DB records must never point at deleted assets.
            workflow.status = "failed"
            workflow.error_code = exc.__class__.__name__[:64]
            workflow.completed_at = timezone.now()
            workflow.save(update_fields=["status", "error_code", "completed_at", "updated_at"])
            raise
        finally:
            if self._owns_vision_provider and hasattr(self.vision_provider, "close"):
                self.vision_provider.close()

    def _step(self, workflow, ordinal, name, function, *args):
        event("floor_plan_" + name, workflowId=str(workflow.id))
        workflow.current_step = name
        workflow.save(update_fields=["current_step", "updated_at"])
        snapshot_hash = hashlib.sha256(json.dumps([self._snapshot_value(arg) for arg in args], sort_keys=True).encode()).hexdigest()
        step = AgentWorkflowStep.objects.create(
            workflow_run=workflow,
            ordinal=ordinal,
            name=name,
            status="running",
            input_snapshot_hash=snapshot_hash,
            started_at=timezone.now(),
        )
        try:
            result = function(*args)
            step.status = "completed"
            step.output = self._summarize_output(result)
            step.confidence = self._confidence(result)
            step.completed_at = timezone.now()
            step.save(update_fields=["status", "output", "confidence", "completed_at", "updated_at"])
            return result
        except Exception as exc:
            step.status = "failed"
            step.error_code = exc.__class__.__name__[:64]
            step.completed_at = timezone.now()
            step.save(update_fields=["status", "error_code", "completed_at", "updated_at"])
            raise

    def _detect(self, ingestion_run, content=None, media_type=None):
        if content and media_type:
            decisions = list(DocumentLayoutPage.objects.filter(document=ingestion_run.document))
            if decisions:
                selected_pages = [
                    SelectedFloorPlanPage(decision.page_number, 12.0, ("explicit_layout_page",))
                    for decision in decisions if decision.state == "layout"
                ]
            else:
                selected_pages = FloorPlanPageSelector().select(content, media_type)
            chunks_by_page = {}
            for chunk in ingestion_run.chunks.order_by("ordinal"):
                chunks_by_page.setdefault(chunk.page_number, []).append(chunk)
            selected = []
            for page in selected_pages:
                page_chunks = chunks_by_page.get(page.page_number, [])
                if page_chunks:
                    chunk = max(page_chunks, key=lambda item: len(item.content))
                    selected.append({
                        "score": min(1.0, page.score / 12.0),
                        "chunk": chunk,
                        "text": " ".join(item.content for item in page_chunks),
                        "selectionScore": page.score,
                        "selectionReasons": list(page.reasons),
                        "explicitLayout": "explicit_layout_page" in page.reasons,
                    })
            if selected or media_type == "application/pdf":
                return selected
        matches = retrieve(ingestion_run.project, "floor plan unit plan layout BHK carpet area bedroom dimensions", limit=12, ingestion_run=ingestion_run)
        detected = [item for item in matches if FLOOR_PLAN_TERMS.search(item["chunk"].content)]
        known_ids = {item["chunk"].id for item in detected}
        for chunk in ingestion_run.chunks.all():
            if chunk.id not in known_ids and (
                chunk.metadata.get("requiresVision")
                or (chunk.metadata.get("imageCount", 0) and not chunk.content.strip())
            ):
                detected.append({"score": 0.5, "chunk": chunk})
                known_ids.add(chunk.id)
        return detected

    def _crop_and_store(self, workflow, contexts, content, media_type):
        if not contexts or not content or not media_type:
            return []
        return FloorPlanImageExtractor().extract_and_store(workflow, content, media_type, contexts)

    def _extract(self, ingestion_run, contexts, assets):
        if getattr(self.vision_provider, "code", None) == "rules" and assets:
            return self._identify_without_ai(contexts, assets)
        candidates = []
        assets_by_page = {}
        for asset in assets:
            assets_by_page.setdefault(asset.page_number, []).append(asset)
        for item in contexts:
            chunk = item["chunk"]
            text = item.get("text", chunk.content)
            page_assets = assets_by_page.get(chunk.page_number, [])
            # Images, not regex guesses, are authoritative for rendered plans.
            if page_assets:
                continue
            configuration = CONFIGURATION_PATTERN.search(text)
            area = AREA_PATTERN.search(text)
            dimensions = [
                {"room": match.group(1).lower(), "lengthFt": float(match.group(2)), "widthFt": float(match.group(3))}
                for match in DIMENSION_PATTERN.finditer(text)
            ]
            if text and (configuration or area or dimensions):
                confidence = min(0.92, 0.45 + 0.15 * bool(configuration) + 0.15 * bool(area) + 0.05 * len(dimensions))
                candidates.append(FloorPlanCandidate(
                    page_number=chunk.page_number,
                    plan_name=f"Floor plan page {chunk.page_number or 1}",
                    configuration_type=configuration.group(1).upper().replace(" ", "") if configuration else "",
                    carpet_area_sqft=self._decimal(area.group(1)) if area else None,
                    dimensions=dimensions,
                    confidence=confidence,
                    evidence_text=text[:4000],
                    asset=page_assets[0] if page_assets else None,
                    plan_scope=self._plan_scope(text),
                ))
        assets = sorted(assets, key=lambda asset: (
            0 if asset.source_bounds.get("category") == "layouts" else
            1 if asset.source_bounds.get("category") == "floor-plans" else 2,
            asset.page_number or 0,
        ))
        for index, asset in enumerate(assets, 1):
            event("floor_plan_request", pageNumber=asset.page_number, imageIndex=index, imageTotal=len(assets))
            with default_storage.open(asset.object_key, "rb") as screenshot:
                analyze = getattr(self.vision_provider, "analyze_classified_plan", self.vision_provider.analyze)
                result = analyze(
                    screenshot.read(),
                    asset.media_type,
                    self._vision_schema(),
                )
            if "pageType" in result:
                asset.source_bounds = asset.source_bounds | {"aiClassification": {
                    "pageType": result["pageType"], "visualEvidence": result["visualEvidence"],
                    "provider": self.vision_provider.code, "model": getattr(self.vision_provider, "model", ""),
                }}
                asset.save(update_fields=["source_bounds", "updated_at"])
            candidates.extend(self._vision_candidates(asset.page_number, result, asset))
        return candidates

    def _identify_without_ai(self, contexts, assets):
        candidates = []
        by_page = {item["chunk"].page_number: item for item in contexts}
        # One record per selected page, not an invented segmentation into individual units.
        sources = [(asset.page_number, asset, by_page.get(asset.page_number, {})) for asset in assets]
        if not assets:
            sources = [(page, None, item) for page, item in by_page.items()]
        for page_number, asset, item in sources:
            chunk = item.get("chunk")
            text = item.get("text", chunk.content if chunk else "")
            scope = classify_plan_scope(text)
            configurations = {match.upper().replace(" ", "") for match in CONFIGURATION_PATTERN.findall(text)}
            areas = {self._decimal(value) for value in AREA_PATTERN.findall(text)} - {None}
            # Page-level labels can refer to multiple units. Do not associate a price/area
            # or room dimension with a guessed flat. Only retain unambiguous flat labels.
            single_flat = scope == "flat" and len(configurations) == 1
            evidence = {
                "method": RULE_VERSION,
                "classification": "likely_plan" if scope != "unknown" else "candidate_page",
                "selectionReasons": item.get("selectionReasons", ["text_or_image_evidence"]),
                "configurationLabels": sorted(configurations),
                "carpetAreaLabelsSqFt": sorted(float(area) for area in areas),
                "textAvailable": bool(text.strip()),
                "unitSegmentationPerformed": False,
            }
            candidates.append(FloorPlanCandidate(
                page_number=page_number,
                plan_name=f"Floor plan page {page_number or 1}",
                configuration_type=next(iter(configurations)) if single_flat else "",
                carpet_area_sqft=next(iter(areas)) if single_flat and len(areas) == 1 else None,
                dimensions=[],
                confidence=min(0.8, float(item.get("score", 0.25))) if scope != "unknown" else 0.25,
                evidence_text=text[:4000], asset=asset, plan_scope=scope,
                features={"identification": evidence},
            ))
        return candidates

    def _validate(self, candidates):
        valid = []
        for candidate in candidates:
            dimensions = [item for item in candidate.dimensions if 0.5 <= item["lengthFt"] <= 100 and 0.5 <= item["widthFt"] <= 100]
            area = candidate.carpet_area_sqft
            confidence = candidate.confidence
            if area is not None and not Decimal("100") <= area <= Decimal("20000"):
                area = None
                confidence *= 0.7
            if candidate.dimensions and not dimensions:
                confidence *= 0.7
            valid.append(FloorPlanCandidate(
                page_number=candidate.page_number,
                plan_name=candidate.plan_name,
                configuration_type=candidate.configuration_type,
                carpet_area_sqft=area,
                dimensions=calculate_room_areas(dimensions),
                confidence=min(1, confidence),
                evidence_text=candidate.evidence_text,
                asset=candidate.asset,
                plan_scope=candidate.plan_scope,
                orientation=candidate.orientation,
                features=candidate.features or {},
            ))
        return valid

    def _reconcile(self, candidates):
        selected = {}
        for candidate in candidates:
            key = (
                candidate.page_number,
                candidate.plan_scope,
                candidate.plan_name.casefold(),
                candidate.configuration_type.casefold(),
            )
            if key not in selected or candidate.confidence > selected[key].confidence:
                selected[key] = candidate
        return list(selected.values())

    @transaction.atomic
    def _persist(self, workflow, candidates):
        floor_plans = []
        ai_generated = self._provider_metadata()["extractionMode"] == "ai"
        for candidate in candidates:
            # Run suffix preserves all pre-existing script/AI rows under the legacy unique key.
            name = f"{candidate.plan_name[:205]} [AI {workflow.id}]" if ai_generated else candidate.plan_name
            existing = FloorPlan.objects.select_for_update().filter(
                document=workflow.document, page_number=candidate.page_number, plan_name=name,
            ).first()
            if existing and existing.review_state in {"removed_by_user", "replaced_by_user", "superseded_page_candidate"}:
                # A replay must not undo an explicit visibility decision or
                # return the hidden record to the review gate.
                continue
            plan, _ = FloorPlan.objects.update_or_create(
                document=workflow.document,
                page_number=candidate.page_number,
                plan_name=name,
                defaults={
                    "project": workflow.project,
                    "workflow_run": workflow,
                    "asset": candidate.asset,
                    "plan_scope": candidate.plan_scope,
                    "configuration_type": candidate.configuration_type,
                    "carpet_area_sqft": candidate.carpet_area_sqft,
                    "room_count": self._room_count(candidate.configuration_type),
                    "bathroom_count": sum(1 for item in candidate.dimensions if item["room"].startswith(("bath", "toilet"))) or None,
                    "balcony_count": sum(1 for item in candidate.dimensions if item["room"].startswith("balcony")) or None,
                    "orientation": candidate.orientation,
                    "dimensions": candidate.dimensions,
                    "features": candidate.features or {},
                    "raw_extraction": {
                        "evidenceText": candidate.evidence_text,
                        "sourcePlanName": candidate.plan_name,
                        "assetId": str(candidate.asset.id) if candidate.asset else None,
                        "identification": (candidate.features or {}).get("identification"),
                        **self._provider_metadata(),
                    },
                    "confidence": candidate.confidence,
                    "review_state": "gen_with_ai_needs_review" if ai_generated else "needs_review",
                    "extractor_version": self.version,
                },
            )
            floor_plans.append(plan)
            self._create_fact_candidates(plan)
        return floor_plans

    def _review_gate(self, workflow, floor_plans):
        for plan in floor_plans:
            ReviewTask.objects.get_or_create(
                project=workflow.project,
                task_type="floor_plan",
                state="open",
                resolution={"floorPlanId": str(plan.id)},
                defaults={"priority": 2},
            )
        return {"createdTasks": len(floor_plans), "autoPublished": False}

    @transaction.atomic
    def _persist_vision_placeholders(self, workflow):
        floor_plans = []
        seen_pages = set()
        for asset in workflow.floor_plan_assets.order_by("page_number", "created_at"):
            if asset.page_number in seen_pages:
                continue
            seen_pages.add(asset.page_number)
            existing = FloorPlan.objects.select_for_update().filter(
                document=workflow.document, page_number=asset.page_number,
                plan_name=f"Floor plan page {asset.page_number or 1}",
            ).first()
            if existing and existing.review_state in {"removed_by_user", "replaced_by_user", "superseded_page_candidate"}:
                continue
            plan, _ = FloorPlan.objects.update_or_create(
                document=workflow.document,
                page_number=asset.page_number,
                plan_name=f"Floor plan page {asset.page_number or 1}",
                defaults={
                    "project": workflow.project,
                    "workflow_run": workflow,
                    "asset": asset,
                    "plan_scope": "unknown",
                    "configuration_type": "",
                    "carpet_area_sqft": None,
                    "room_count": None,
                    "bathroom_count": None,
                    "balcony_count": None,
                    "orientation": "",
                    "dimensions": [],
                    "features": {},
                    "raw_extraction": {"assetId": str(asset.id), "requiresVision": True, **self._provider_metadata()},
                    "confidence": asset.detection_confidence,
                    "review_state": "needs_review",
                    "extractor_version": self.version,
                },
            )
            floor_plans.append(plan)
        return floor_plans

    def _create_fact_candidates(self, plan):
        values = {
            "configuration_type": plan.configuration_type,
            "carpet_area_sqft": float(plan.carpet_area_sqft) if plan.carpet_area_sqft is not None else None,
            "room_dimensions": plan.dimensions,
        }
        for field_code, value in values.items():
            if value in (None, "", []):
                continue
            FactCandidate.objects.create(
                project=plan.project,
                document=plan.document,
                field_code=field_code,
                typed_value=value,
                unit="sqft" if field_code == "carpet_area_sqft" else "",
                page_number=plan.page_number,
                source_class="developer",
                confidence=plan.confidence,
                extractor_version=self.version,
                review_state="unreviewed",
            )

    @staticmethod
    def _decimal(value):
        try:
            number = Decimal(value)
            return number if number.is_finite() else None
        except (InvalidOperation, TypeError):
            return None

    @staticmethod
    def _room_count(configuration):
        match = re.match(r"(\d+)", configuration)
        return int(match.group(1)) if match else None

    @staticmethod
    def _summarize_output(result):
        if isinstance(result, list):
            if result and isinstance(result[0], dict) and "chunk" in result[0]:
                return {"itemCount": len(result), "pages": [
                    {"pageNumber": item["chunk"].page_number,
                     "score": item.get("selectionScore", item.get("score", 0)),
                     "reasons": item.get("selectionReasons", ["text_or_image_evidence"])}
                    for item in result
                ], "selectorVersion": FloorPlanPageSelector.version}
            return {"itemCount": len(result)}
        if isinstance(result, dict):
            return result
        return {"type": result.__class__.__name__}

    @staticmethod
    def _confidence(result):
        if isinstance(result, list) and result:
            values = [getattr(item, "confidence", item.get("score", 0) if isinstance(item, dict) else 0) for item in result]
            return min(1, sum(values) / len(values))
        return 1 if result else 0

    @staticmethod
    def _vision_schema():
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["floorPlans"],
            "properties": {
                "floorPlans": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "planName", "planScope", "configurationType", "carpetAreaSqFt",
                            "orientation", "dimensions", "features", "confidence",
                        ],
                        "properties": {
                            "planName": {"type": "string"},
                            "planScope": {"type": "string", "enum": ["flat", "floor", "unknown"]},
                            "configurationType": {"type": "string"},
                            "carpetAreaSqFt": {"type": ["number", "null"]},
                            "orientation": {"type": ["string", "null"]},
                            "dimensions": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["room", "lengthFt", "widthFt"],
                                    "properties": {
                                        "room": {"type": "string"},
                                        "lengthFt": {"type": "number"},
                                        "widthFt": {"type": "number"},
                                    },
                                },
                            },
                            "features": {"type": "array", "items": {"type": "string"}},
                            "confidence": {"type": "number"},
                        },
                    },
                },
            },
        }

    def _vision_candidates(self, page_number, result, asset=None):
        if not isinstance(result, dict) or not isinstance(result.get("floorPlans", []), list):
            raise ValueError("INVALID_VISION_RESPONSE")
        try:
            serialized_size = len(json.dumps(result, separators=(",", ":"), default=str).encode())
        except (TypeError, ValueError) as exc:
            raise ValueError("INVALID_VISION_RESPONSE") from exc
        if serialized_size > 1_000_000:
            raise ValueError("VISION_RESPONSE_TOO_LARGE")
        candidates = []
        for item in result.get("floorPlans", [])[:20]:
            if not isinstance(item, dict):
                continue
            dimensions = []
            raw_dimensions = item.get("dimensions", [])
            if isinstance(raw_dimensions, list):
                for dimension in raw_dimensions[:50]:
                    if not isinstance(dimension, dict):
                        continue
                    room = str(dimension.get("room", ""))[:64]
                    try:
                        length = float(dimension.get("lengthFt"))
                        width = float(dimension.get("widthFt"))
                    except (TypeError, ValueError):
                        continue
                    if room and 0.5 <= length <= 100 and 0.5 <= width <= 100:
                        dimensions.append({"room": room, "lengthFt": length, "widthFt": width})
            raw_features = item.get("features", [])
            features = {
                "items": [str(feature)[:128] for feature in raw_features[:50] if str(feature).strip()]
            } if isinstance(raw_features, list) else {}
            try:
                confidence = float(item.get("confidence", 0))
            except (TypeError, ValueError):
                confidence = 0
            if not math.isfinite(confidence):
                confidence = 0
            candidates.append(FloorPlanCandidate(
                page_number=page_number,
                plan_name=str(item.get("planName", f"Floor plan page {page_number}"))[:255],
                configuration_type=str(item.get("configurationType", ""))[:32],
                carpet_area_sqft=self._decimal(str(item.get("carpetAreaSqFt"))) if item.get("carpetAreaSqFt") is not None else None,
                dimensions=dimensions,
                confidence=max(0, min(1, confidence)),
                evidence_text=str(result.get("visualEvidence", ""))[:4000],
                asset=asset,
                plan_scope=item.get("planScope") if item.get("planScope") in {"flat", "floor", "unknown"} else "unknown",
                orientation=str(item.get("orientation") or "")[:32],
                features=features,
            ))
        if result.get("pageType") == "floor_plan" and candidates:
            identified_units = [{
                "planName": candidate.plan_name,
                "configurationType": candidate.configuration_type,
                "carpetAreaSqFt": float(candidate.carpet_area_sqft) if candidate.carpet_area_sqft is not None else None,
            } for candidate in candidates]
            return [FloorPlanCandidate(
                page_number=page_number,
                plan_name=f"Floor plan page {page_number or 1}",
                configuration_type="",
                carpet_area_sqft=None,
                dimensions=[],
                confidence=max(candidate.confidence for candidate in candidates),
                evidence_text=str(result.get("visualEvidence", ""))[:4000],
                asset=asset,
                plan_scope="floor",
                features={"identifiedUnits": identified_units, "pageType": "floor_plan"},
            )]
        return candidates

    def _provider_metadata(self):
        return {
            "extractionMode": "script" if getattr(self.vision_provider, "code", None) == "rules" else "ai",
            "visionProvider": getattr(self.vision_provider, "code", "custom"),
            "visionModel": getattr(self.vision_provider, "model", ""),
            "visionProfileId": getattr(self.vision_provider, "profile_id", None),
        }

    @staticmethod
    def _plan_scope(text):
        normalized = text.casefold()
        if re.search(r"\b(typical\s*floor|floor\s*plan|tower\s*plan|wing\s*plan)\b", normalized):
            return "floor"
        if re.search(r"\b(unit\s*plan|flat\s*plan|apartment\s*plan|\d(?:\.5)?\s*bhk)\b", normalized):
            return "flat"
        return "unknown"

    @classmethod
    def _snapshot_value(cls, value):
        if isinstance(value, bytes):
            return {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
        if isinstance(value, list):
            return [cls._snapshot_value(item) for item in value]
        if isinstance(value, dict):
            return {str(key): cls._snapshot_value(item) for key, item in value.items()}
        if hasattr(value, "pk"):
            return {"model": value.__class__.__name__, "id": str(value.pk)}
        return str(value)[:500]
