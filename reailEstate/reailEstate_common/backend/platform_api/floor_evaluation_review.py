"""Reviewed corrections to whole-floor observations, separate from flat facts."""
from copy import deepcopy
import math

from django.db import models, transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from .builder_views import BuilderConflict
from .ingestion_runtime import WorkAlreadyClaimed, work_lock
from .infrastructure.source_documents import read_source
from .jobs.events import EXTRACTION_EVENTS
from .layout_crop_update import StaleCropEdit, _check_version, _read_asset, _version, editable_layout
from .models import EvidenceDocument, FloorPlan, FloorPlanAsset, OutboxMessage, Project, ReviewTask
from .services import audit


class FloorInput(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise ValidationError("Use only the supported floor-evaluation fields.")
        values = super().to_internal_value(data)
        if any(isinstance(value, float) and not math.isfinite(value) for value in values.values()):
            raise ValidationError("Floor measurements must be finite numbers.")
        return values


class CommonAreaInput(FloorInput):
    kind = serializers.CharField(max_length=64)
    label = serializers.CharField(max_length=200, allow_blank=True)
    count = serializers.IntegerField(min_value=0, max_value=10000, allow_null=True)
    area = serializers.FloatField(min_value=0, max_value=1_000_000_000, allow_null=True)
    unit = serializers.ChoiceField(choices=["sqft", "sqm"], allow_null=True)
    evidence = serializers.CharField(max_length=3000, allow_blank=True)

    def validate(self, data):
        if data["area"] is not None and data["unit"] is None:
            raise ValidationError("Choose a unit for the measured area.")
        if data["area"] is None:
            data["unit"] = None
        return data


class RefugeInput(FloorInput):
    presence = serializers.ChoiceField(choices=["present", "absent", "unknown"])
    area = serializers.FloatField(min_value=0, max_value=1_000_000_000, allow_null=True)
    unit = serializers.ChoiceField(choices=["sqft", "sqm"], allow_null=True)
    evidence = serializers.CharField(max_length=3000, allow_blank=True)

    def validate(self, data):
        if data["presence"] != "present" and data["area"] is not None:
            raise ValidationError("An area requires a refuge marked present.")
        if data["area"] is not None and data["unit"] is None:
            raise ValidationError("Choose a unit for the refuge area.")
        if data["area"] is None:
            data["unit"] = None
        return data


class FloorEvaluationInput(FloorInput):
    flatsPerFloor = serializers.IntegerField(min_value=0, max_value=10000, allow_null=True)
    liftCount = serializers.IntegerField(min_value=0, max_value=10000, allow_null=True)
    staircaseCount = serializers.IntegerField(min_value=0, max_value=10000, allow_null=True)
    refugeArea = RefugeInput()
    commonAreas = CommonAreaInput(many=True, max_length=50)
    visualEvidence = serializers.CharField(max_length=10000, allow_blank=True)


def review_floor_evaluation(request, snapshot, reason):
    if request.data.get("scope") != "floor" or "fields" in request.data or "rooms" in request.data:
        raise ValidationError("Use the floor-evaluation form for a whole-floor plan.")
    with work_lock("document", snapshot.document_id):
        current, destination = editable_layout(snapshot.pk, request.user)
        if current.document_id != snapshot.document_id or current.updated_at != snapshot.updated_at:
            raise BuilderConflict("This floor plan changed. Reload before reviewing it.")
        version = _version(current, destination)
        # Verify bytes outside the database transaction while retaining document ownership.
        try:
            read_source(current.document)
            _read_asset(current.asset)
        except OSError:
            raise ValidationError("The source image or document is unavailable. Restore it before review.") from None
        return _save_floor_review(request, current, reason, version)


def _save_floor_review(request, snapshot, reason, version):
    from .admin_workspace_views import independent
    decision = request.data["decision"]
    with transaction.atomic():
        document = EvidenceDocument.objects.select_for_update().get(pk=snapshot.document_id)
        locked = FloorPlan.objects.select_for_update().get(pk=snapshot.pk)
        Project.objects.select_for_update().get(pk=version["project_id"])
        FloorPlanAsset.objects.select_for_update().get(pk=version["expected_asset_id"])
        if locked.document_id != snapshot.document_id or locked.updated_at != snapshot.updated_at:
            raise BuilderConflict("This floor plan changed. Reload before reviewing it.")
        plan, destination = editable_layout(locked.pk, request.user)
        try:
            _check_version(plan, destination, version)
        except StaleCropEdit as exc:
            raise BuilderConflict(str(exc)) from None
        if plan.plan_scope != "floor" or not plan.active or destination.status in {"retired", "merged"}:
            raise ValidationError("Choose an active whole-floor plan.")
        if OutboxMessage.objects.filter(event_type__in=EXTRACTION_EVENTS, published_at__isnull=True,
                dead_lettered_at__isnull=True).filter(models.Q(payload__floorPlanId=str(plan.pk)) |
                models.Q(payload__documentId=str(document.pk))).exists():
            raise WorkAlreadyClaimed("This floor plan has pending evaluation work.")
        features = plan.features if isinstance(plan.features, dict) else {}
        before = features.get("floorEvaluation", {})
        if not isinstance(before, dict):
            raise ValidationError("Saved floor observations are malformed. Re-evaluate the source.")
        if (before.get("assetId") and before["assetId"] != str(plan.asset_id)) or (
                before.get("assetSha256") and before["assetSha256"] != plan.asset.sha256) or (
                before.get("sourceSha256") and before["sourceSha256"] != document.sha256) or (
                plan.asset.source_bounds.get("sourceSha256") and plan.asset.source_bounds["sourceSha256"] != document.sha256):
            raise BuilderConflict("The floor evaluation belongs to an older source. Re-evaluate it first.")
        now = timezone.now()
        if decision == "save-correction":
            values = FloorEvaluationInput(data=request.data.get("floorEvaluation"))
            values.is_valid(raise_exception=True)
            corrected = values.validated_data
            if all(before.get(key) == value for key, value in corrected.items()):
                return {"status": "unchanged"}
            after = {**deepcopy(before), **corrected, "assetId": str(plan.asset_id),
                "assetSha256": plan.asset.sha256, "sourceSha256": document.sha256,
                "correctedBy": request.user.pk, "correctedAt": now.isoformat(), "reviewState": "unreviewed"}
            for key in ("reviewedBy", "reviewedAt", "reviewReason"):
                after.pop(key, None)
            plan.features = {**features, "floorEvaluation": after}
            plan.review_state = "needs_review"
            plan.save(update_fields=["features", "review_state", "updated_at"])
            task = ReviewTask.objects.create(project=destination, task_type="floor_plan", priority=1,
                resolution={"floorPlanId": str(plan.pk), "source": "manual_floor_evaluation", "submittedBy": request.user.pk,
                    "reviewState": "unreviewed", "reason": reason, "before": before, "after": after})
            audit(request, "floor_plan.floor_evaluation_corrected", plan,
                metadata={"reason": reason, "reviewTaskId": str(task.pk)})
            return {"status": "saved_for_review"}
        independent(request.user, destination, submitted_by=before.get("correctedBy"))
        if not before:
            raise ValidationError("Save a floor evaluation before reviewing it.")
        state = "approved" if decision == "approve" else "rejected"
        plan.features = {**features, "floorEvaluation": {**before, "reviewState": state,
            "reviewedBy": request.user.pk, "reviewedAt": now.isoformat(), "reviewReason": reason}}
        plan.review_state = state
        plan.save(update_fields=["features", "review_state", "updated_at"])
        for task in ReviewTask.objects.select_for_update().filter(project=destination, state="open",
                task_type__in=["floor_plan", "floor_plan_vision"], resolution__floorPlanId=str(plan.pk)):
            task.state = "resolved" if decision == "approve" else "rejected"
            task.completed_at = now
            task.version += 1
            task.resolution = {**task.resolution, "reviewState": state, "decision": decision, "reason": reason}
            task.save(update_fields=["state", "completed_at", "version", "resolution", "updated_at"])
        audit(request, "floor_plan.floor_evaluation_reviewed", plan, metadata={"decision": decision, "reason": reason})
        return {"status": state}
