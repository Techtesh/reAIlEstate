"""Project workspace adapter for the shared immutable, identity-preserving crop revision service."""

import hashlib
from uuid import uuid4

from django.core import signing
from django.core.files.base import ContentFile

from . import layout_crop_update as edits
from .artifact_storage import storage
from .feature_flags import require_enabled
from .floor_plan_visibility import HIDDEN_REVIEW_STATES
from .infrastructure.source_documents import render_source_pages
from .layout_image_normalization import _normalized_png
from .models import LayoutCropDraft
from .project_workspace import project_evidence_ids
from .policies.crops import CART_LIMIT, MAX_DRAFT_BYTES, authorize_crops


def check_plan(plan, project, actor):
    authorize_crops(actor, project)
    if (project.status in {"retired", "merged"} or plan.project_id not in project_evidence_ids(project)
            or plan.document.project_id != plan.project_id or plan.document.scan_status != "clean"
            or not plan.asset_id or plan.asset.project_id not in project_evidence_ids(project)
            or plan.asset.document_id != plan.document_id or plan.asset.status != "ready"
            or plan.review_state in HIDDEN_REVIEW_STATES or not plan.active):
        raise ValueError("Choose an active, clean, ready plan from this project.")
    require_enabled("media.crop_update", actor=actor, project=project)


def prepare_revision(plan, project, actor, *, regions=None, rotation=0, normalize=False, source_mode="original"):
    """Called under document ownership; original rectangles use full-page coordinates."""
    check_plan(plan, project, actor)
    if normalize:
        source = edits._read_asset(plan.asset)
        crop, size, box, margin = _normalized_png(source, rotation if rotation <= 180 else rotation - 360)
        rectangle = {"x": 0, "y": 0, "width": 1, "height": 1}
        source_asset_id, mode = str(plan.asset_id), "current"
    else:
        source = edits._read_asset(plan.asset) if source_mode == "current" else render_source_pages(plan.document, [plan.page_number])[0][1]
        rectangle = edits.validate_rectangle(regions)
        crop, size, box = edits._render_crop(source, rectangle, (-rotation) % 360)
        source_asset_id, mode, margin = (str(plan.asset_id) if source_mode == "current" else None), source_mode, None
    state = {
        **edits._version(plan, project), "actor_id": str(actor.pk),
        "source_mode": mode, "source_asset_id": source_asset_id,
        "source_sha256": hashlib.sha256(source).hexdigest(), "page_number": plan.page_number,
    }
    return {"state": state, "token": signing.dumps(state, salt=edits.TOKEN_SALT, compress=True),
            "rectangle": rectangle, "rotation": (-rotation) % 360,
            "image": crop, "sha256": hashlib.sha256(crop).hexdigest(),
            "size": size, "pixelBox": box, "margin": margin}


def stage_revision(plan, project, actor, prepared):
    require_enabled("media.crop_drafts", actor=actor, project=project)
    if LayoutCropDraft.objects.filter(owner=actor).count() >= CART_LIMIT:
        raise ValueError("Your crop cart holds at most 20 drafts. Attach or discard some first.")
    if len(prepared["image"]) > MAX_DRAFT_BYTES:
        raise ValueError("Crop exceeds 20 MiB; select a smaller region.")
    edits._check_version(plan, project, prepared["state"])
    return LayoutCropDraft.objects.create(
        owner=actor, project=project, source_plan=plan, label=plan.display_title[:64],
        source_sha256=plan.document.sha256, image=prepared["image"], sha256=prepared["sha256"],
        pixel_width=prepared["size"][0], pixel_height=prepared["size"][1],
        bounds={"draftAction": "update", "category": "floor-plans" if plan.plan_scope == "floor" else "layouts",
                "builderRevision": {key: value for key, value in prepared.items() if key != "image"}},
    )


def prepare_saved_revision(draft, plan, project, actor):
    check_plan(plan, project, actor)
    prepared = dict(draft.bounds.get("builderRevision", {}))
    legacy = not prepared and bool(draft.bounds.get("editorState"))
    if legacy:
        state = draft.bounds["editorState"]
        if state.get("actor_id") != str(actor.pk) or state.get("project_id") != str(project.pk):
            raise ValueError("Crop update draft does not belong to this workspace.")
        source = edits._checked_preview(plan, project, state)
        parameters = draft.bounds["parameters"]
        rectangle = edits.validate_rectangle(parameters["regions"])
        rotation = edits.validate_rotation(parameters.get("rotation_degrees", 0))
        image, size, box = edits._render_crop(source, rectangle, rotation)
        prepared = {"state": state, "token": signing.dumps(state, salt=edits.TOKEN_SALT, compress=True),
            "rectangle": rectangle, "rotation": rotation, "sha256": hashlib.sha256(image).hexdigest(),
            "size": size, "pixelBox": box, "margin": None}
    try:
        # A staged draft remains usable after the editor's one-hour session expires.
        state = signing.loads(prepared["token"], salt=edits.TOKEN_SALT)
    except (signing.BadSignature, KeyError, TypeError):
        raise ValueError("Invalid crop update draft; discard it and crop again.") from None
    if (state != prepared.get("state") or state["actor_id"] != str(actor.pk)
            or state["plan_id"] != str(plan.pk) or state["project_id"] != str(project.pk)):
        raise ValueError("Crop update draft does not belong to this plan.")
    edits._check_version(plan, project, state)
    source = edits._checked_preview(plan, project, state) if legacy else (
        edits._read_asset(plan.asset) if state["source_mode"] == "current" else render_source_pages(plan.document, [plan.page_number])[0][1]
    )
    if hashlib.sha256(source).hexdigest() != state["source_sha256"]:
        raise edits.StaleCropEdit("Source page changed; discard the stale draft and crop again.")
    image = bytes(draft.image)
    if hashlib.sha256(image).hexdigest() != draft.sha256 or draft.sha256 != prepared["sha256"]:
        raise ValueError("Draft checksum mismatch.")
    prepared["image"] = image
    return prepared


def commit_revision(plan, project, actor, prepared, saved_keys, vision_profile_id=None):
    """Caller holds document/plan locks and the publication/idempotency transaction."""
    check_plan(plan, project, actor)
    require_enabled("media.extraction_queue", actor=actor, project=project)
    edits._check_version(plan, project, prepared["state"])
    asset_id = uuid4()
    key = storage.save(f"output/media/images/{project.pk}/crop-revisions/{plan.pk}/{asset_id}.png",
                       ContentFile(prepared["image"]))
    saved_keys.append(key)
    return edits._persist(
        plan, project, actor, prepared["state"], prepared["rectangle"], prepared["rotation"],
        prepared["image"], prepared["size"], prepared["pixelBox"], asset_id, key,
        vision_profile_id=vision_profile_id,
    )
