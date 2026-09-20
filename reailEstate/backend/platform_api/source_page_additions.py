"""Explicit source-page additions. Source files and prior extraction remain unchanged."""

import hashlib
import io
from pathlib import Path
from uuid import uuid4

import pymupdf
from PIL import Image, ImageOps
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from .artifact_storage import storage
from .feature_flags import FeatureDisabled, is_enabled, require_enabled
from .floor_plan_extractor import FloorPlanImageExtractor
from .floor_plan_vision import resolve_ai_vision_selection
from .ingestion_runtime import work_lock
# Compatibility reexports. Keep the shared storage/settings/imaging symbols above
# available for existing callers and patches of their methods/attributes.
from .infrastructure.source_documents import (
    SOURCE_TYPES, read_source, render_source_pages, source_page_count,
)
from .models import (
    AgentWorkflowRun, AuditEvent, DocumentLayoutPage, EvidenceDocument, FloorPlan, FloorPlanAsset,
    IngestionRun, Operation, OutboxMessage, ReviewTask,
)
from .project_lifecycle import project_family_ids
from .policies.crops import crop_destination


PAGE_EVENT = "source.page_evaluation.requested"
CATEGORIES = {"layouts": "Layout", "floor-plans": "Floor plan", "amenities": "Amenity"}


def _authorize(actor, project):
    from .models import OrganizationMembership
    if not actor or not actor.is_active:
        raise PermissionDenied
    if actor.is_staff and actor.has_perm("platform_api.change_project"):
        return
    if not OrganizationMembership.objects.filter(
        organization=project.organization, user=actor, status="active", role__in=["owner", "editor"]
    ).exists():
        raise PermissionDenied


def _queue_page(asset, category, actor, selection, *, retry=False, force=False):
    """Caller holds the document lock and a transaction; reuse historical additions."""
    existing = OutboxMessage.objects.filter(event_type=PAGE_EVENT, payload__assetId=str(asset.pk)).order_by("-occurred_at", "-pk").first()
    if existing:
        operation = Operation.objects.get(pk=existing.payload["operationId"])
        if force and operation.state == "completed" and existing.published_at:
            existing = None
    if existing:
        if retry and existing.dead_lettered_at and operation.state == "failed":
            existing.dead_lettered_at = None
            existing.available_after = None
            existing.attempts = 0
            existing.last_error_code = ""
            existing.save()
            operation.state = "pending"
            operation.error_code = ""
            operation.save()
        if category == "layouts" and operation.state == "completed":
            from .floor_plan_visibility import visible_floor_plans
            skipped = _queue_automatic_layouts(
                asset, visible_floor_plans(FloorPlan.objects.filter(asset=asset, plan_scope="floor")),
                actor, existing.payload["visionSelection"], retry=retry,
            )
            if skipped:
                asset.workflow_run.result = asset.workflow_run.result | skipped
                asset.workflow_run.save(update_fields=["result", "updated_at"])
                operation.result = operation.result | skipped
                operation.save(update_fields=["result", "updated_at"])
        return existing
    placeholder = None
    if category != "amenities":
        placeholder = FloorPlan.objects.create(
            project=asset.project, document=asset.document, workflow_run=asset.workflow_run, asset=asset,
            page_number=asset.page_number, plan_scope="flat" if category == "layouts" else "floor",
            plan_name=f"{CATEGORIES[category]} page {asset.page_number} [manual {asset.workflow_run_id}]",
            review_state="pending_page_evaluation", extractor_version="manual-source-page-1.0",
            raw_extraction={"manualPageAddition": True, "isPageCandidate": True},
        )
    operation = Operation.objects.create(owner=actor, kind="source_page_evaluation", result={"assetId": str(asset.pk)})
    payload = {"projectId": str(asset.project_id), "documentId": str(asset.document_id),
               "assetId": str(asset.pk), "assetSha256": asset.sha256,
               "operationId": str(operation.pk), "visionSelection": selection, "category": category}
    if placeholder:
        payload["floorPlanId"] = str(placeholder.pk)
    message = OutboxMessage.objects.create(event_type=PAGE_EVENT, correlation_id=uuid4(), payload=payload)
    asset.workflow_run.status = "queued"
    asset.workflow_run.save(update_fields=["status", "updated_at"])
    AuditEvent.objects.create(actor=actor, action="document.source_page_queued", entity_type="FloorPlanAsset",
                              entity_id=asset.pk, correlation_id=message.correlation_id,
                              metadata={"category": category, "messageId": str(message.pk)})
    return message


def _queue_automatic_layouts(asset, plans, actor, selection, *, retry=False):
    """Optional fanout only; disabling admission must not fail page classification."""
    from .floor_plan_layout_jobs import queue_floor_plan_layouts

    destination = crop_destination(asset.project)
    skipped = {"autoSegmentationSkipped": "FEATURE_DISABLED", "autoSegmentationFeature": "media.auto_segment"}
    if not is_enabled("media.auto_segment", actor=actor, project=destination):
        return skipped
    for plan in plans:
        if plan.plan_scope != "floor":
            continue
        try:
            queue_floor_plan_layouts(plan, actor, vision_selection=selection, retry=retry)
        except FeatureDisabled as exc:
            # A flag can change between the optional decision and queue admission.
            if exc.decision.key not in {"media.auto_segment", "media.extraction_queue"}:
                raise
            return skipped
    return {}


def queue_saved_amenity_pages(project, actor, vision_profile_id=None):
    """Recover only operator-added amenity pages; never enqueue whole documents."""
    _authorize(actor, project)
    if project.status in {"retired", "merged"}:
        raise ValueError("PROJECT_NOT_ACTIVE")
    destination = crop_destination(project)
    require_enabled("media.source_pages", actor=actor, project=destination)
    require_enabled("media.extraction_queue", actor=actor, project=destination)
    selection = resolve_ai_vision_selection(vision_profile_id)
    jobs = []
    for asset in FloorPlanAsset.objects.filter(
        project_id__in=project_family_ids(project), status="ready",
        source_bounds__manualPageAddition=True, source_bounds__category="amenities",
    ).select_related("document", "workflow_run", "project").order_by("document_id", "page_number"):
        with work_lock("document", asset.document_id), transaction.atomic():
            document = EvidenceDocument.objects.select_for_update().get(pk=asset.document_id)
            if document.scan_status != "clean" or document.sha256 != asset.source_bounds.get("sourceSha256"):
                raise ValueError("STALE_SOURCE_PAGE")
            jobs.append(_queue_page(asset, "amenities", actor, selection, retry=True))
    return jobs


def add_source_pages(project, document_id, pages, category, actor, vision_profile_id=None, *, mark_layout=False, notes="",
                     expected_source_sha256=None, expected_page_sha256=None):
    _authorize(actor, project)
    destination = crop_destination(project)
    require_enabled("media.source_pages", actor=actor, project=destination)
    require_enabled("media.extraction_queue", actor=actor, project=destination)
    if category not in CATEGORIES:
        raise ValueError("Choose layout, amenity or floor-plan pages.")
    if not isinstance(pages, list) or not pages or len(pages) > 24 or any(type(p) is not int for p in pages):
        raise ValueError("Select between 1 and 24 pages.")
    pages = sorted(set(pages))
    document = EvidenceDocument.objects.get(id=document_id, project_id__in=project_family_ids(project))
    if project.status in {"retired", "merged"}:
        raise ValueError("Choose an active canonical project.")
    # Every explicit addition enters page-specific extraction, including amenities.
    selection = resolve_ai_vision_selection(vision_profile_id)
    with work_lock("document", document.id):
        rendered = render_source_pages(document, pages)  # No database transaction during rendering.
        if expected_source_sha256 is not None and document.sha256 != expected_source_sha256:
            raise ValueError("Source changed; discard the stale page draft.")
        if expected_page_sha256 is not None and any(
            hashlib.sha256(data).hexdigest() != expected_page_sha256.get(page) for page, data in rendered
        ):
            raise ValueError("Source page changed; discard the stale page draft.")
        saved_keys, additions = [], []
        try:
            with transaction.atomic():
                locked = EvidenceDocument.objects.select_for_update().get(id=document.id)
                if locked.sha256 != document.sha256 or locked.scan_status != "clean":
                    raise ValueError("Source changed; reload the document picker.")
                for page, data in rendered:
                    if mark_layout and category == "layouts":
                        decision, _ = DocumentLayoutPage.objects.update_or_create(
                            document=document, page_number=page,
                            defaults={"state": "layout", "source": "user_flag", "reason_code": "MISSED_LAYOUT",
                                      "notes": str(notes)[:1000], "marked_by": actor},
                        )
                        AuditEvent.objects.create(
                            actor=actor, action="document.layout_page_marked", entity_type="DocumentLayoutPage",
                            entity_id=decision.id, correlation_id=uuid4(),
                            metadata={"documentId": str(document.id), "pageNumber": page},
                        )
                    existing = FloorPlanAsset.objects.filter(
                        document=document, page_number=page, status="ready",
                        source_bounds__manualPageAddition=True,
                        source_bounds__category__in=["layouts", "floor-plans"] if category != "amenities" else [category],
                        source_bounds__sourceSha256=document.sha256,
                    ).order_by("created_at", "pk").first()
                    if existing:
                        job = _queue_page(existing, category, actor, selection, retry=True)
                        additions.append({"assetId": str(existing.id), "page": page, "existing": True, "messageId": str(job.pk)})
                        continue
                    now = timezone.now()
                    run = IngestionRun.objects.create(
                        project=document.project, document=document, status="needs_review",
                        pipeline_version="manual-source-page-1.0", extraction_provider="manual",
                        started_at=now, completed_at=now,
                        summary={"scope": "manual_source_page", "pageNumber": page, "category": category},
                    )
                    workflow = AgentWorkflowRun.objects.create(
                        project=document.project, document=document, ingestion_run=run,
                        workflow_type="manual_source_page", status="queued" if selection else "completed",
                        started_at=now, result={"category": category, "manualPageAddition": True},
                    )
                    key = storage.save(
                        f"output/media/images/{document.project_id}/{category}/{workflow.id}/{uuid4().hex}.png",
                        ContentFile(data),
                    )
                    saved_keys.append(key)
                    with Image.open(io.BytesIO(data)) as image:
                        width, height = image.size
                    asset = FloorPlanAsset.objects.create(
                        project=document.project, document=document, workflow_run=workflow, page_number=page,
                        asset_type="page_render", object_key=key, byte_length=len(data),
                        sha256=hashlib.sha256(data).hexdigest(), pixel_width=width, pixel_height=height,
                        detector_version="manual-source-page-1.0", detection_confidence=0,
                        source_bounds={"category": category, "manualPageAddition": True,
                                       "sourceSha256": document.sha256, "markedBy": actor.pk,
                                       "unit": "normalized", "x": 0, "y": 0, "width": 1, "height": 1},
                    )
                    job = _queue_page(asset, category, actor, selection)
                    ReviewTask.objects.create(
                        project=document.project, task_type="source_page", priority=1,
                        resolution={"assetId": str(asset.id), "pageNumber": page, "category": category},
                    )
                    AuditEvent.objects.create(
                        actor=actor, action="document.source_page_added", entity_type="FloorPlanAsset",
                        entity_id=asset.id, correlation_id=uuid4(),
                        metadata={"documentId": str(document.id), "pageNumber": page, "category": category},
                    )
                    additions.append({"assetId": str(asset.id), "page": page, "existing": False, "messageId": str(job.pk)})
            return additions
        except Exception:
            for key in saved_keys:
                storage.delete(key)
            raise


def evaluate_source_page(payload):
    from .floor_plan_agent import FloorPlanAgent
    from .floor_plan_vision import get_floor_plan_vision_provider

    with work_lock("document", payload["documentId"]):
        operation = Operation.objects.get(id=payload["operationId"])
        if operation.state in {"completed", "cancelled"}:
            return
        provider = None
        try:
            asset = FloorPlanAsset.objects.select_related("document", "workflow_run", "project").get(id=payload["assetId"])
            if asset.document.scan_status != "clean" or asset.status != "ready" or asset.sha256 != payload["assetSha256"]:
                raise ValueError("STALE_SOURCE_PAGE")
            if asset.project.status in {"retired", "merged"}:
                raise ValueError("PROJECT_RETIRED")
            if FloorPlan.objects.filter(pk=payload.get("floorPlanId"), review_state__in=["removed_by_user", "replaced_by_user"]).exists():
                return
            with storage.open(asset.object_key, "rb") as stream:
                data = stream.read(asset.byte_length + 1)
            if len(data) != asset.byte_length or hashlib.sha256(data).hexdigest() != asset.sha256:
                raise ValueError("IMAGE_INTEGRITY_MISMATCH")
            operation.state = "running"
            operation.save(update_fields=["state", "updated_at"])
            AgentWorkflowRun.objects.filter(pk=asset.workflow_run_id).update(status="running")
            provider = get_floor_plan_vision_provider(payload["visionSelection"])
            if payload["category"] == "amenities":
                from .source_page_amenities import evaluate_amenity_page
                evaluate_amenity_page(asset, operation, provider, data)
                return
            agent = FloorPlanAgent(provider)
            result = provider.analyze_classified_plan(data, asset.media_type, agent._vision_schema())
            candidates = agent._reconcile(agent._validate(agent._vision_candidates(asset.page_number, result, asset)))
            with transaction.atomic():
                operation.refresh_from_db()
                if operation.state == "cancelled" or FloorPlan.objects.filter(
                    pk=payload.get("floorPlanId"), review_state__in=["removed_by_user", "replaced_by_user"]
                ).exists():
                    return
                workflow = asset.workflow_run
                plans = agent._persist(workflow, candidates)
                agent._review_gate(workflow, plans)
                skipped = _queue_automatic_layouts(asset, plans, operation.owner, payload["visionSelection"])
                FloorPlan.objects.filter(id=payload["floorPlanId"]).update(review_state="superseded_page_candidate")
                workflow.status = "completed"
                workflow.completed_at = timezone.now()
                workflow.result = workflow.result | {"classification": result, "floorPlanIds": [str(p.id) for p in plans]} | skipped
                workflow.save()
                operation.state = "completed"
                operation.error_code = ""
                operation.result = {"assetId": str(asset.id), "planIds": [str(p.id) for p in plans],
                                    "outcome": "needs_review" if plans else "no_plan_detected", **skipped}
                operation.save()
        except Exception as exc:
            Operation.objects.filter(pk=operation.pk).exclude(state="cancelled").update(
                state="failed", error_code=type(exc).__name__[:64], updated_at=timezone.now())
            AgentWorkflowRun.objects.filter(floor_plan_assets__id=payload["assetId"]).update(status="failed")
            raise
        finally:
            if provider and hasattr(provider, "close"):
                provider.close()