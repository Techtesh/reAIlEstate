"""Draft-only crops from original evidence; materialize provenance only on Attach."""

import hashlib
import io
import math
from uuid import UUID

from PIL import Image
from django.core.files.base import ContentFile
from django.db import transaction

from .artifact_storage import storage
from .domain.geometry import crop_region, validate_regions
from .feature_flags import require_enabled
from .ingestion_runtime import work_lock
from .models import EvidenceDocument, FloorPlanAsset, LayoutCropDraft, Project
from .policies.crops import CART_LIMIT, MAX_DRAFT_BYTES, authorize_crops as _authorize, crop_destination
from .project_lifecycle import project_family_ids
from .source_page_additions import render_source_pages


def checked_document(project, document_id, source_sha256, *, lock=False):
    """A preview checksum is mandatory; never silently accept the current revision."""
    query = EvidenceDocument.objects.select_for_update() if lock else EvidenceDocument.objects
    try:
        document = query.get(pk=document_id, project_id__in=project_family_ids(project))
    except EvidenceDocument.DoesNotExist:
        raise ValueError("Choose a source document from this project family.") from None
    if not source_sha256 or document.sha256 != source_sha256 or document.scan_status != "clean":
        raise ValueError("Source changed or is not clean; reload the source page and crop again.")
    return document


def original_page_bounds(region, rotation, original_size, rotated_size):
    """Retain the inverse-rotated polygon as well as its clipped bounding box."""
    width, height = original_size
    rotated_width, rotated_height = rotated_size
    angle = math.radians(rotation)
    cosine, sine = math.cos(angle), math.sin(angle)
    polygon = []
    for x, y in ((region.x, region.y), (region.x + region.width, region.y),
                 (region.x + region.width, region.y + region.height), (region.x, region.y + region.height)):
        dx, dy = x * rotated_width - rotated_width / 2, y * rotated_height - rotated_height / 2
        polygon.append([(cosine * dx - sine * dy + width / 2) / width,
                        (sine * dx + cosine * dy + height / 2) / height])
    # Polygon corners may include the rotated canvas padding; page bounds cannot.
    clamp = lambda value: min(1, max(0, value))
    left, right = clamp(min(p[0] for p in polygon)), clamp(max(p[0] for p in polygon))
    top, bottom = clamp(min(p[1] for p in polygon)), clamp(max(p[1] for p in polygon))
    return {"unit": "normalized", "x": left, "y": top, "width": right - left,
            "height": bottom - top, "polygon": polygon}


def stage_page_crops(project, document_id, page_number, raw_regions, actor, source_sha256, rotation_degrees=0):
    project = crop_destination(project)
    _authorize(actor, project)
    require_enabled("media.crop_drafts", actor=actor, project=project)
    try:
        document_id = UUID(str(document_id))
    except (TypeError, ValueError):
        raise ValueError("Choose a valid source document.") from None
    if type(page_number) is not int or page_number < 1:
        raise ValueError("Select a valid source page.")
    if type(rotation_degrees) is not int or not -180 <= rotation_degrees < 360 or rotation_degrees % 5:
        raise ValueError("Invalid rotation.")
    regions = validate_regions(raw_regions, allow_overlap=True)
    if len({(r.x, r.y, r.width, r.height) for r in regions}) != len(regions):
        raise ValueError("Each crop must select a different rectangle.")
    if not regions or len(regions) > CART_LIMIT:
        raise ValueError("Select between 1 and 20 individual crops, not whole pages.")
    with work_lock("crop-cart", actor.pk), work_lock("document", document_id):
        current_project = Project.objects.get(pk=project.pk)
        _authorize(actor, current_project)
        if LayoutCropDraft.objects.filter(owner=actor).count() + len(regions) > CART_LIMIT:
            raise ValueError("Your crop cart holds at most 20 drafts across all projects. Attach or discard some first.")
        document = checked_document(current_project, document_id, source_sha256)
        # This renderer checks original bytes and renders exactly this page, not the document.
        data = render_source_pages(document, [page_number])[0][1]
        return _save_page_drafts(current_project, document, page_number, regions, actor, source_sha256, rotation_degrees, data)


def _save_page_drafts(current_project, document, page_number, regions, actor, source_sha256, rotation_degrees, data):
    # Rendering is outside the transaction; the caller still owns cart/document locks.
    with transaction.atomic():
        current_project = Project.objects.select_for_update().get(pk=current_project.pk)
        _authorize(actor, current_project)
        document = checked_document(current_project, document.pk, source_sha256, lock=True)
        page_sha256 = hashlib.sha256(data).hexdigest()
        drafts = []
        with Image.open(io.BytesIO(data)) as original:
            original_size = original.size
            image = original.convert("RGB")
            if rotation_degrees:
                rotated = image.rotate(rotation_degrees, expand=True, resample=Image.Resampling.BICUBIC, fillcolor="white")
                image.close()
                image = rotated
            try:
                for region in regions:
                    left, top = round(region.x * image.width), round(region.y * image.height)
                    right = min(image.width, round((region.x + region.width) * image.width))
                    bottom = min(image.height, round((region.y + region.height) * image.height))
                    if right <= left or bottom <= top or (left, top, right, bottom) == (0, 0, image.width, image.height):
                        raise ValueError("Select an individual crop with visible pixels, not the whole page.")
                    crop, box, size = crop_region(image, region)
                    if len(crop) > MAX_DRAFT_BYTES:
                        raise ValueError("Crop exceeds 20 MiB; select a smaller region.")
                    draft = LayoutCropDraft.objects.create(
                        owner=actor, project=current_project, document=document, page_number=page_number,
                        source_sha256=source_sha256, label=region.label, image=crop,
                        sha256=hashlib.sha256(crop).hexdigest(), pixel_width=size[0], pixel_height=size[1],
                        bounds={"unit": "normalized", "x": region.x, "y": region.y,
                                "width": region.width, "height": region.height, "pixelBox": list(box),
                                "rotationDegreesCounterClockwise": rotation_degrees,
                                "originalPageBounds": original_page_bounds(region, rotation_degrees, original_size, image.size),
                                "originalPageSize": list(original_size), "sourcePageSha256": page_sha256,
                                "sourceDocumentId": str(document.pk), "sourceSha256": source_sha256,
                                "pageNumber": page_number, "floors": region.floors,
                                "evidence": "reviewer_selected_boundary"},
                    )
                    drafts.append(str(draft.pk))
            finally:
                image.close()
        # Save writes ONLY LayoutCropDraft: no assets, plans, workflows, audit or queue.
        return {"draftIds": drafts, "state": "draft", "planIds": [], "queued": [],
                "cartCount": LayoutCropDraft.objects.filter(owner=actor).count()}


def prepare_page_source(project, draft):
    """Called under cart/document ownership, BEFORE the attachment transaction."""
    document = checked_document(project, draft.document_id, draft.source_sha256)
    data = render_source_pages(document, [draft.page_number])[0][1]
    digest = hashlib.sha256(data).hexdigest()
    if digest != draft.bounds.get("sourcePageSha256"):
        raise ValueError("Source page rendering changed; discard the stale draft and crop again.")
    return document, data, digest


def materialize_page_source(project, draft, workflow, prepared, saved_keys):
    """Reuse an intact full-page asset or create evidence only, NEVER a FloorPlan."""
    document, data, digest = prepared
    existing = FloorPlanAsset.objects.filter(
        project_id__in=project_family_ids(project), document=document, page_number=draft.page_number,
        asset_type="page_render", status="ready", sha256=digest,
    ).order_by("created_at", "pk")
    for asset in existing:
        try:
            with storage.open(asset.object_key, "rb") as stream:
                stored = stream.read(asset.byte_length + 1)
        except OSError:
            continue
        if len(stored) == len(data) == asset.byte_length and hashlib.sha256(stored).hexdigest() == digest:
            return asset
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
    key = storage.save(f"output/media/images/{project.pk}/source-pages/{workflow.pk}/{draft.page_number}.png", ContentFile(data))
    saved_keys.append(key)
    return FloorPlanAsset.objects.create(
        project=project, document=document, workflow_run=workflow, page_number=draft.page_number,
        asset_type="page_render", object_key=key, byte_length=len(data), sha256=digest,
        pixel_width=width, pixel_height=height, detector_version="manual-page-crop-cart-1.0",
        detection_confidence=0,
        source_bounds={"sourceOnly": True, "sourceSha256": draft.source_sha256,
                       "sourceDocumentId": str(document.pk), "pageNumber": draft.page_number,
                       "unit": "normalized", "x": 0, "y": 0, "width": 1, "height": 1},
    )
