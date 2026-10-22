"""Normalize derived layout images without changing immutable source evidence."""

import hashlib
from uuid import uuid4

from django.core.files.base import ContentFile
from django.db import transaction
from realestate_common.images import normalized_png as _normalized_png

from .artifact_storage import storage
from .feature_flags import FeatureDisabled, require_enabled
from .floor_plan_visibility import HIDDEN_REVIEW_STATES
from .models import AuditEvent, FloorPlan
from .policies.crops import authorize_crops, crop_destination
from .project_lifecycle import project_family_ids


ALLOWED_ROTATIONS = {-180, -135, -90, -45, 0, 45, 90, 135, 180}


def normalize_layout_image(floor_plan_id, rotation_degrees, actor=None):
    if isinstance(rotation_degrees, bool) or rotation_degrees not in ALLOWED_ROTATIONS:
        raise ValueError("INVALID_LAYOUT_ROTATION")
    plan = FloorPlan.objects.select_related("asset", "project", "document").get(id=floor_plan_id)
    destination = crop_destination(plan.project)
    authorize_crops(actor, destination)
    asset = plan.asset
    family = project_family_ids(destination)
    if (plan.review_state in HIDDEN_REVIEW_STATES or plan.document.scan_status != "clean"
            or plan.document.project_id not in family or plan.document.project.status == "retired"
            or (asset and (asset.project_id not in family or asset.document_id != plan.document_id
                           or asset.project.status == "retired"))):
        raise ValueError("Choose an active, clean layout in the canonical project family.")
    try:
        require_enabled("operations.legacy_destructive", actor=actor, project=destination)
    except FeatureDisabled as exc:
        exc.args = ("FEATURE_DISABLED: operations.legacy_destructive. "
                    "Use the immutable Update this unit crop UI to rotate or revise a layout without overwriting evidence.",)
        raise
    if plan.plan_scope != "flat" or asset is None or asset.asset_type != "unit_crop" or asset.status != "ready":
        raise ValueError("DERIVED_LAYOUT_REQUIRED")
    with storage.open(asset.object_key, "rb") as source:
        data = source.read(asset.byte_length + 1)
    if len(data) != asset.byte_length or hashlib.sha256(data).hexdigest() != asset.sha256:
        raise ValueError("IMAGE_INTEGRITY_MISMATCH")
    normalized, size, trim_box, margin = _normalized_png(data, rotation_degrees)
    digest = hashlib.sha256(normalized).hexdigest()
    old_key = asset.object_key
    prior = asset.source_bounds.get("displayNormalization", {})
    history = list(asset.source_bounds.get("displayNormalizationHistory", []))
    if prior:
        history.append(prior)
    prior_rotation = prior.get(
        "cumulativeRotationDegreesCounterClockwise", prior.get("rotationDegreesCounterClockwise", 0)
    )
    cumulative_rotation = (int(prior_rotation) + rotation_degrees) % 360
    if cumulative_rotation > 180:
        cumulative_rotation -= 360
    key = storage.save(
        f"output/media/images/{asset.project_id}/layouts/{asset.source_bounds.get('sourceAssetId', asset.id)}/normalized/"
        f"{asset.id}-{uuid4().hex}.png",
        ContentFile(normalized),
    )
    try:
        with transaction.atomic():
            asset.object_key = key
            asset.byte_length = len(normalized)
            asset.sha256 = digest
            asset.pixel_width, asset.pixel_height = size
            asset.source_bounds = asset.source_bounds | {
                "displayNormalization": {
                    "rotationDegreesCounterClockwise": rotation_degrees,
                    "cumulativeRotationDegreesCounterClockwise": cumulative_rotation,
                    "trimBoxBeforeMargin": trim_box,
                    "marginPixels": margin,
                    "previousSha256": hashlib.sha256(data).hexdigest(),
                },
                "displayNormalizationHistory": history[-20:],
            }
            asset.detector_version = f"{asset.detector_version}+display-normalization-1.0"[:64]
            asset.save(update_fields=[
                "object_key", "byte_length", "sha256", "pixel_width", "pixel_height",
                "source_bounds", "detector_version", "updated_at",
            ])
            plan.raw_extraction = plan.raw_extraction | {
                "displayNormalization": asset.source_bounds["displayNormalization"],
            }
            plan.save(update_fields=["raw_extraction", "updated_at"])
            AuditEvent.objects.create(
                actor=actor, action="floor_plan.layout_image_normalized", entity_type="FloorPlan",
                entity_id=plan.id, correlation_id=uuid4(),
                metadata={"assetId": str(asset.id), "rotationDegreesCounterClockwise": rotation_degrees},
            )
            transaction.on_commit(lambda: storage.delete(old_key), robust=True)
    except Exception:
        storage.delete(key)
        raise
    return plan