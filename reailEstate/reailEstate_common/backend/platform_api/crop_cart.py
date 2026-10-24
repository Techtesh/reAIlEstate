"""Stage up to twenty crops per user; attach only on explicit user confirmation."""

import hashlib
import io
import json
from contextlib import ExitStack
from uuid import UUID, uuid4

from PIL import Image
from django.core import signing
from django.core.files.base import ContentFile
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from .artifact_storage import storage
from .domain.geometry import crop_region, validate_regions
from .feature_flags import require_enabled
from .ingestion_runtime import work_lock
from .models import AgentWorkflowRun, AuditEvent, EvidenceDocument, FloorPlan, FloorPlanAsset, IngestionRun, LayoutCropDraft, Operation, OutboxMessage, Project, ReviewTask
from .policies.crops import (
    CART_LIMIT as CART_LIMIT,
    MAX_DRAFT_BYTES as MAX_DRAFT_BYTES,
    authorize_crops as _authorize,
    crop_destination as crop_destination,
)
from .project_lifecycle import project_family_ids


def stage_crop_update(plan_id, actor, **parameters):
    from . import layout_crop_update as edits
    if parameters.get("confirm") is not True:
        raise ValueError("Confirm saving this update as a draft.")
    rectangle = edits.validate_rectangle(parameters["regions"])
    rotation = edits.validate_rotation(parameters.get("rotation_degrees", 0))
    state = edits._unsign(parameters["editor_token"], plan_id, actor)
    for key in ("expected_asset_id", "expected_sha256", "source_sha256", "source_mode"):
        if str(parameters[key]) != str(state[key]):
            raise edits.StaleCropEdit("Source changed; reload the crop editor.")
    plan, destination = edits.editable_layout(plan_id, actor, state["project_id"])
    require_enabled("media.crop_drafts", actor=actor, project=destination)
    require_enabled("media.crop_update", actor=actor, project=destination)
    with work_lock("crop-cart", actor.pk), work_lock("document", plan.document_id):
        if LayoutCropDraft.objects.filter(owner=actor).count() >= CART_LIMIT:
            raise ValueError("Your crop cart is full. Commit or discard drafts first.")
        plan, destination = edits.editable_layout(plan_id, actor, state["project_id"])
        data = edits._checked_preview(plan, destination, state)
        crop, size, box = edits._render_crop(data, rectangle, rotation)
        if len(crop) > MAX_DRAFT_BYTES:
            raise ValueError("Crop exceeds 20 MiB; select a smaller region.")
        with transaction.atomic():
            FloorPlan.objects.select_for_update().get(pk=plan_id)
            plan, destination = edits.editable_layout(plan_id, actor, state["project_id"])
            edits._check_version(plan, destination, state)
            saved_parameters = json.loads(json.dumps(parameters, cls=DjangoJSONEncoder))
            saved_parameters.pop("editor_token")
            draft = LayoutCropDraft.objects.create(
                owner=actor, project=destination, source_plan=plan, label=plan.display_title[:64],
                source_sha256=state["source_sha256"], image=crop,
                sha256=hashlib.sha256(crop).hexdigest(), pixel_width=size[0], pixel_height=size[1],
                bounds={"draftAction": "update", "editorState": state,
                        "parameters": saved_parameters, "pixelBox": list(box)},
            )
    return draft


def stage_source_pages(project, document_id, pages, category, actor, vision_profile_id=None):
    from .crop_from_page import checked_document
    from .source_page_additions import render_source_pages
    project = crop_destination(project)
    _authorize(actor, project)
    require_enabled("media.crop_drafts", actor=actor, project=project)
    require_enabled("media.source_pages", actor=actor, project=project)
    if category not in {"floor-plans", "amenities"}:
        raise ValueError("Choose floor-plan or amenity pages.")
    if not isinstance(pages, list) or not pages or any(type(page) is not int or page < 1 for page in pages):
        raise ValueError("Select valid source pages.")
    pages = sorted(set(pages))
    with work_lock("crop-cart", actor.pk), work_lock("document", document_id):
        if LayoutCropDraft.objects.filter(owner=actor).count() + len(pages) > CART_LIMIT:
            raise ValueError("Your crop cart holds at most 20 drafts. Commit or discard some first.")
        document = EvidenceDocument.objects.get(pk=document_id, project_id__in=project_family_ids(project))
        document = checked_document(project, document_id, document.sha256)
        rendered = render_source_pages(document, pages)
        with transaction.atomic():
            document = checked_document(project, document_id, document.sha256, lock=True)
            drafts = []
            for page, data in rendered:
                if len(data) > MAX_DRAFT_BYTES:
                    raise ValueError("Page exceeds the 20 MiB draft limit.")
                with Image.open(io.BytesIO(data)) as image:
                    width, height = image.size
                digest = hashlib.sha256(data).hexdigest()
                drafts.append(LayoutCropDraft.objects.create(
                    owner=actor, project=project, document=document, page_number=page,
                    source_sha256=document.sha256, label=f"{category}: page {page}", image=data,
                    sha256=digest, pixel_width=width, pixel_height=height,
                    bounds={"draftAction": "page", "category": category,
                            "sourcePageSha256": digest, "visionProfileId": vision_profile_id},
                ))
    return drafts


def commit_saved_draft(project, actor, draft_id):
    from . import layout_crop_update as edits
    from .source_page_additions import add_source_pages
    _authorize(actor, project)
    with work_lock("crop-cart", actor.pk):
        draft = LayoutCropDraft.objects.filter(pk=draft_id, owner=actor, project=project).first()
        if draft is None:
            raise ValueError("Select a draft from your project cart.")
        if hashlib.sha256(bytes(draft.image)).hexdigest() != draft.sha256:
            raise ValueError("Draft checksum mismatch.")
        action = draft.bounds.get("draftAction")
        if action == "update":
            state = draft.bounds["editorState"]
            if state["project_id"] != str(project.pk):
                raise ValueError("Draft destination changed; discard it and crop again.")
            parameters = dict(draft.bounds["parameters"], confirm=True)
            parameters["editor_token"] = signing.dumps(state, salt=edits.TOKEN_SALT, compress=True)
            edits.update_layout_crop(draft.source_plan_id, actor, **parameters)
        elif action == "page":
            add_source_pages(project, draft.document_id, [draft.page_number], draft.bounds["category"],
                             actor, draft.bounds.get("visionProfileId"),
                             expected_source_sha256=draft.source_sha256,
                             expected_page_sha256={draft.page_number: draft.sha256})
        else:
            raise ValueError("Use Add selected to project for new layout crops.")
        draft.delete()
    return action


def stage_crops(plan, raw_regions, actor, rotation_degrees=0):
    destination = crop_destination(plan.project)
    _authorize(actor, destination)
    require_enabled("media.crop_drafts", actor=actor, project=destination)
    if type(rotation_degrees) is not int or not -180 <= rotation_degrees < 360 or rotation_degrees % 5:
        raise ValueError("Invalid rotation.")
    regions = validate_regions(raw_regions, allow_overlap=True)
    if not regions or len(regions) > CART_LIMIT:
        raise ValueError("Select between 1 and 20 crops.")
    with work_lock("crop-cart", actor.pk), work_lock("document", plan.document_id):
        if LayoutCropDraft.objects.filter(owner=actor).count() + len(regions) > CART_LIMIT:
            raise ValueError("Your crop cart holds at most 20 drafts. Add or discard some before saving more.")
        plan = FloorPlan.objects.select_related("asset", "document", "project").get(pk=plan.pk)
        destination = crop_destination(plan.project)
        _authorize(actor, destination)
        require_enabled("media.crop_drafts", actor=actor, project=destination)
        asset = plan.asset
        if not asset or asset.status != "ready" or plan.document.scan_status != "clean":
            raise ValueError("Source image must be ready and clean.")
        family_ids = project_family_ids(destination)
        if (plan.review_state in {"removed_by_user", "replaced_by_user"}
                or plan.document.project_id not in family_ids or asset.project_id not in family_ids
                or asset.document_id != plan.document_id):
            raise ValueError("The crop source is removed or outside the destination project family.")
        with storage.open(asset.object_key, "rb") as stream:
            data = stream.read(asset.byte_length + 1)
        if len(data) != asset.byte_length or hashlib.sha256(data).hexdigest() != asset.sha256:
            raise ValueError("Source image checksum mismatch.")
        prepared = []
        with Image.open(io.BytesIO(data)) as original:
            image = original.convert("RGB")
            if rotation_degrees:
                rotated = image.rotate(rotation_degrees, expand=True, resample=Image.Resampling.BICUBIC, fillcolor="white")
                image.close()
                image = rotated
            try:
                for region in regions:
                    crop, box, size = crop_region(image, region)
                    if len(crop) > MAX_DRAFT_BYTES:
                        raise ValueError("Crop exceeds 20 MiB; select a smaller region.")
                    prepared.append((region, crop, box, size))
            finally:
                image.close()
        with transaction.atomic():
            destination = Project.objects.select_for_update().get(pk=destination.pk)
            _authorize(actor, destination)
            if crop_destination(plan.project).pk != destination.pk:
                raise ValueError("Project merge changed; reload the crop editor.")
            drafts = []
            for region, crop, box, size in prepared:
                draft = LayoutCropDraft.objects.create(
                    owner=actor, project=destination, source_plan=plan,
                    source_sha256=asset.sha256, label=region.label,
                    image=crop, sha256=hashlib.sha256(crop).hexdigest(),
                    pixel_width=size[0], pixel_height=size[1],
                    bounds={"unit": "normalized", "x": region.x, "y": region.y,
                            "width": region.width, "height": region.height, "pixelBox": list(box),
                            "rotationDegreesCounterClockwise": rotation_degrees,
                            "sourceAssetId": str(asset.id), "sourcePlanId": str(plan.pk),
                            "floors": region.floors, "evidence": "reviewer_selected_boundary"},
                )
                drafts.append(str(draft.pk))
            AuditEvent.objects.create(actor=actor, action="layout.crop_drafts_saved", entity_type="Project",
                                      entity_id=destination.pk, correlation_id=uuid4(),
                                      metadata={"draftIds": drafts, "sourceProjectId": str(plan.project_id)})
        return {"draftIds": drafts, "state": "draft", "cartCount": LayoutCropDraft.objects.filter(owner=actor).count(),
                "planIds": [], "queued": [], "projectId": str(destination.pk)}


def _ids(values):
    try:
        result = {UUID(str(value)) for value in values}
    except (TypeError, ValueError):
        raise ValueError("Invalid selection.") from None
    if len(result) > CART_LIMIT:
        raise ValueError("Select at most 20 entries.")
    return result


def discard_drafts(project, actor, draft_ids):
    _authorize(actor, project)
    ids = _ids(draft_ids)
    with work_lock("crop-cart", actor.pk), transaction.atomic():
        drafts = LayoutCropDraft.objects.filter(owner=actor, project=project, pk__in=ids)
        if not ids or drafts.count() != len(ids):
            raise ValueError("Select your draft crops from this project.")
        drafts.delete()
        AuditEvent.objects.create(actor=actor, action="layout.crop_drafts_discarded", entity_type="Project",
                                  entity_id=project.pk, correlation_id=uuid4(), metadata={"draftIds": list(map(str, ids))})


def attach_drafts(project, actor, draft_ids, replacement_ids=(), vision_profile_id=None):
    from .crop_from_page import checked_document, materialize_page_source, prepare_page_source
    from .floor_plan_vision import resolve_ai_vision_selection
    from .layout_reanalysis import queue_layout_evaluation
    _authorize(actor, project)
    destination = crop_destination(project)
    require_enabled("media.crop_attach", actor=actor, project=destination)
    require_enabled("media.extraction_queue", actor=actor, project=destination)
    ids, replacements = _ids(draft_ids), _ids(replacement_ids)
    if not ids:
        raise ValueError("Select at least one draft crop.")
    selection = resolve_ai_vision_selection(vision_profile_id)
    keys = []
    with work_lock("crop-cart", actor.pk), ExitStack() as locks:
        drafts = list(LayoutCropDraft.objects.filter(owner=actor, project=project, pk__in=ids).select_related(
            "source_plan__asset", "source_plan__document", "document"
        ))
        if len(drafts) != len(ids):
            raise ValueError("Some drafts have already been added/discarded or belong to another cart.")
        if any(draft.bounds.get("draftAction") for draft in drafts):
            raise ValueError("Use Commit saved pages/updates for page additions and crop updates.")
        targets = list(FloorPlan.objects.filter(project=project, pk__in=replacements, plan_scope="flat"))
        if len(targets) != len(replacements):
            raise ValueError("Select existing layouts from this project to replace.")
        doc_ids = {d.document_id if d.document_id else d.source_plan.document_id for d in drafts} | {p.document_id for p in targets}
        for doc_id in sorted(doc_ids, key=str):
            locks.enter_context(work_lock("document", doc_id))
        prepared_pages = {}
        for draft in drafts:
            if draft.document_id:
                page_key = (draft.document_id, draft.page_number, draft.source_sha256)
                if page_key not in prepared_pages:
                    prepared_pages[page_key] = prepare_page_source(project, draft)
        try:
            with transaction.atomic():
                current_project = Project.objects.select_for_update().get(pk=project.pk)
                _authorize(actor, current_project)
                targets = list(FloorPlan.objects.select_for_update().filter(pk__in=replacements, project=project, plan_scope="flat"))
                if len(targets) != len(replacements):
                    raise ValueError("Replacement selection changed; reload the cart.")
                new_ids = []
                correlation = uuid4()
                page_sources = {}
                for draft in drafts:
                    page_key = None
                    if draft.document_id:
                        page_key = (draft.document_id, draft.page_number, draft.source_sha256)
                        document = checked_document(current_project, draft.document_id, draft.source_sha256, lock=True)
                        _, _, page_digest = prepared_pages[page_key]
                        if draft.bounds.get("sourcePageSha256") != page_digest:
                            raise ValueError("Source changed; discard the stale draft and crop again.")
                    else:
                        source = FloorPlanAsset.objects.select_for_update().select_related("document").filter(
                            pk=draft.source_plan.asset_id,
                            document_id=draft.source_plan.document_id,
                            project_id__in=project_family_ids(current_project),
                            document__project_id__in=project_family_ids(current_project),
                        ).first()
                        if not source or source.sha256 != draft.source_sha256 or source.status != "ready" or source.document.scan_status != "clean":
                            raise ValueError("Source changed; discard the stale draft and crop again.")
                        with storage.open(source.object_key, "rb") as stream:
                            original = stream.read(source.byte_length + 1)
                        if len(original) != source.byte_length or hashlib.sha256(original).hexdigest() != source.sha256:
                            raise ValueError("Source image checksum mismatch.")
                        document = source.document
                    data = bytes(draft.image)
                    if hashlib.sha256(data).hexdigest() != draft.sha256:
                        raise ValueError("Draft checksum mismatch.")
                    now = timezone.now()
                    run = IngestionRun.objects.create(project=project, document=document,
                        pipeline_version="manual-crop-cart-1.0", extraction_provider="manual", status="needs_review",
                        started_at=now, completed_at=now, summary={"scope": "manual_crop_cart", "draftId": str(draft.pk)})
                    workflow = AgentWorkflowRun.objects.create(project=project, document=document, ingestion_run=run,
                        workflow_type="manual_crop_addition", status="completed", started_at=now, completed_at=now,
                        result={"segmentationMethod": "manual", "draftId": str(draft.pk)})
                    if page_key is not None:
                        if page_key not in page_sources:
                            page_sources[page_key] = materialize_page_source(
                                project, draft, workflow, prepared_pages[page_key], keys,
                            )
                        source = page_sources[page_key]
                    workflow.result = workflow.result | {"sourceAssetId": str(source.pk)}
                    workflow.save(update_fields=["result", "updated_at"])
                    bounds = draft.bounds | {"sourceAssetId": str(source.pk)}
                    key = storage.save(f"output/media/images/{project.pk}/layouts/{source.pk}/manual/{draft.pk}.png", ContentFile(data))
                    keys.append(key)
                    category = draft.bounds.get("category", "layouts")
                    if category not in {"layouts", "floor-plans", "amenities", "project-images"}:
                        raise ValueError("Invalid crop category; discard this draft and crop again.")
                    asset = FloorPlanAsset.objects.create(project=project, document=source.document, workflow_run=workflow,
                        page_number=source.page_number, asset_type="unit_crop" if category == "layouts" else "plan_crop",
                        object_key=key, byte_length=len(data),
                        sha256=draft.sha256, pixel_width=draft.pixel_width, pixel_height=draft.pixel_height,
                        source_bounds=bounds | {"category": category, "segmentationMethod": "manual",
                            "draftId": str(draft.pk), "manualPageAddition": category == "amenities",
                            "sourceSha256": source.document.sha256},
                        detector_version="manual-crop-cart-1.0", detection_confidence=0)
                    if category in {"amenities", "project-images"}:
                        if category == "amenities":
                            from .source_page_additions import _queue_page
                            _queue_page(asset, category, actor, selection)
                        continue
                    plan = FloorPlan.objects.create(project=project, document=source.document, workflow_run=workflow,
                        asset=asset, page_number=source.page_number, plan_scope="flat" if category == "layouts" else "floor",
                        plan_name=f"{draft.label} [manual {draft.pk}]", display_name=draft.label, confidence=0,
                        raw_extraction={"segmentationMethod": "manual", "sourcePlanName": draft.label,
                                        "draftId": str(draft.pk), "boundingBox": bounds},
                        review_state="manual_crop_needs_evaluation", extractor_version="manual-crop-cart-1.0")
                    if category == "floor-plans":
                        from .floor_plan_layout_jobs import queue_floor_plan_layouts
                        queue_floor_plan_layouts(plan, actor, vision_selection=selection)
                    else:
                        queue_layout_evaluation(plan, actor=actor, vision_selection=selection)
                    ReviewTask.objects.create(project=project, task_type="floor_plan", priority=1,
                                              resolution={"floorPlanId": str(plan.pk)})
                    new_ids.append(str(plan.pk))
                # Only an explicit user replacement affects the selected existing layouts.
                for old in targets:
                    old.raw_extraction = old.raw_extraction | {"userReplacement": {
                        "previousReviewState": old.review_state, "replacementPlanIds": new_ids,
                        "actorId": actor.pk, "at": timezone.now().isoformat()}}
                    old.review_state = "replaced_by_user"
                    old.save(update_fields=["raw_extraction", "review_state", "updated_at"])
                    pending = OutboxMessage.objects.filter(event_type="floor_plan.crop_evaluation.requested",
                        payload__floorPlanId=str(old.pk), published_at__isnull=True, dead_lettered_at__isnull=True)
                    operation_ids = [m.payload.get("operationId") for m in pending]
                    Operation.objects.filter(pk__in=[pk for pk in operation_ids if pk]).update(
                        state="cancelled", error_code="LAYOUT_REPLACED_BY_USER")
                    pending.update(dead_lettered_at=timezone.now(), last_error_code="LAYOUT_REPLACED_BY_USER")
                AuditEvent.objects.create(actor=actor, action="layout.user_replacement" if replacements else "layout.drafts_added",
                    entity_type="Project", entity_id=project.pk, correlation_id=correlation,
                    metadata={"draftIds": list(map(str, ids)), "planIds": new_ids, "replacedPlanIds": list(map(str, replacements))})
                LayoutCropDraft.objects.filter(owner=actor, project=project, pk__in=ids).delete()
            return new_ids
        except Exception:
            for key in keys:
                storage.delete(key)
            raise
