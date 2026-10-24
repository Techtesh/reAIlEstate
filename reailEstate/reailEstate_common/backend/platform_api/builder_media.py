"""Shared private project media and explicit, permission-scoped processing actions."""

from datetime import timedelta
from contextlib import ExitStack
import hashlib
import json
from uuid import uuid4

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from .artifact_storage import storage
from .builder_views import BuilderConflict, BuilderView, StrictInput, builder_projects, require_new_review
from .crop_cart import attach_drafts, discard_drafts
from .crop_from_page import stage_page_crops
from .domain.room_dimensions import calculate_room_areas, room_rectangle_sum_sqft
from .feature_flags import is_enabled, require_enabled
from .floor_plan_visibility import HIDDEN_REVIEW_STATES, visible_floor_plans
from .floor_plan_vision import list_ai_floor_plan_vision_profiles
from .ingestion_runtime import WorkAlreadyClaimed, work_lock
from .infrastructure.source_documents import SOURCE_TYPES, render_source_pages, source_page_count
from .layout_pricing import project_price_label
from .layout_evaluation_status import layout_evaluation_states
from .layout_reanalysis import queue_layout_evaluation
from .models import EvidenceDocument, FloorPlan, FloorPlanAsset, IdempotencyRecord, LayoutCropDraft, OutboxMessage, Project, QueueWorker
from .queue_monitor import LIVE_SECONDS, project_jobs
from .project_pictures import first_picture_document
from .project_workspace import project_evidence_ids
from .services import audit
from .source_page_additions import add_source_pages
from . import project_media_edits


def scoped_project(user, project_id, *, edit=False, workspace="builder"):
    project = get_object_or_404(builder_projects(user, workspace=workspace), pk=project_id)
    if edit and project.status in {"retired", "merged"}:
        raise BuilderConflict("This project is archived and cannot be changed.")
    return project


def scoped_document(project, document_id, sha256):
    document = get_object_or_404(EvidenceDocument, pk=document_id, project_id__in=project_evidence_ids(project))
    if document.scan_status != "clean" or document.sha256 != sha256:
        raise BuilderConflict("Source changed or is not ready; refresh media before continuing.")
    return document


def private_assets(project):
    # Unlike the public gallery, the editor needs removed/inactive evidence for restore.
    family = project_evidence_ids(project)
    return FloorPlanAsset.objects.filter(
        project_id__in=family, document__project_id__in=family, status="ready", document__scan_status="clean",
        media_type__in=["image/png", "image/jpeg", "image/webp"],
    )


def scoped_plan(project, plan_id, *, lock=False):
    query = FloorPlan.objects.select_for_update() if lock else FloorPlan.objects
    family = project_evidence_ids(project)
    plan = get_object_or_404(query, pk=plan_id, project_id__in=family, document__project_id__in=family)
    if plan.asset_id and (plan.asset.project_id not in family or plan.asset.document_id != plan.document_id):
        raise NotFound("This plan's evidence is unavailable in this project.")
    return plan


def check_version(plan, expected):
    if plan.updated_at != expected:
        raise BuilderConflict("This plan changed. Refresh media before continuing.")


class CropRegion(StrictInput):
    label = serializers.CharField(max_length=64)
    x = serializers.FloatField(min_value=0, max_value=1)
    y = serializers.FloatField(min_value=0, max_value=1)
    width = serializers.FloatField(min_value=0, max_value=1)
    height = serializers.FloatField(min_value=0, max_value=1)


class CropInput(StrictInput):
    documentId = serializers.UUIDField()
    pageNumber = serializers.IntegerField(min_value=1)
    sourceSha256 = serializers.RegexField(r"^[a-f0-9]{64}$")
    regions = CropRegion(many=True, min_length=1, max_length=20)
    rotationDegrees = serializers.ChoiceField(choices=list(range(-180, 360, 5)), default=0)
    requestId = serializers.UUIDField()
    category = serializers.ChoiceField(choices=["layouts", "floor-plans", "amenities", "project-images"], default="layouts")
    sourceMode = serializers.ChoiceField(choices=["original", "current"], default="original")
    targetPlanId = serializers.UUIDField(required=False)
    expectedUpdatedAt = serializers.DateTimeField(required=False)

    def validate(self, attrs):
        if ("targetPlanId" in attrs) != ("expectedUpdatedAt" in attrs):
            raise serializers.ValidationError("Crop updates require targetPlanId and expectedUpdatedAt together.")
        if "targetPlanId" in attrs and len(attrs["regions"]) != 1:
            raise serializers.ValidationError("Updating an existing plan requires exactly one rectangle.")
        if "targetPlanId" not in attrs and (attrs["rotationDegrees"] % 90 or attrs["sourceMode"] != "original"):
            raise serializers.ValidationError("New crops require an original source and right-angle rotation.")
        return attrs


class ConfirmedInput(StrictInput):
    confirm = serializers.BooleanField()
    fresh = serializers.BooleanField(default=False)
    requestId = serializers.UUIDField()
    visionProfileId = serializers.CharField(required=False, max_length=128)

    def validate_confirm(self, value):
        if not value:
            raise serializers.ValidationError("Confirm this action before continuing.")
        return value


class MediaTarget(StrictInput):
    planId = serializers.UUIDField()
    expectedUpdatedAt = serializers.DateTimeField()


class DraftInput(ConfirmedInput):
    action = serializers.ChoiceField(choices=["attach", "discard"])
    draftIds = serializers.ListField(child=serializers.UUIDField(), min_length=1, max_length=20)
    replacementIds = serializers.ListField(child=serializers.UUIDField(), max_length=20, required=False)
    replacementTargets = MediaTarget(many=True, max_length=20, required=False)

    def validate(self, attrs):
        if len(set(attrs["draftIds"])) != len(attrs["draftIds"]):
            raise serializers.ValidationError("Select each draft once.")
        if attrs["action"] == "discard" and attrs.get("replacementIds"):
            raise serializers.ValidationError("Discard cannot replace layouts.")
        if set(attrs.get("replacementIds", [])) != {item["planId"] for item in attrs.get("replacementTargets", [])}:
            raise serializers.ValidationError("Replacement layouts require their current versions.")
        return attrs


class EvaluationInput(ConfirmedInput):
    planId = serializers.UUIDField()
    expectedUpdatedAt = serializers.DateTimeField()


class PagesInput(ConfirmedInput):
    documentId = serializers.UUIDField()
    sourceSha256 = serializers.RegexField(r"^[a-f0-9]{64}$")
    pages = serializers.ListField(child=serializers.IntegerField(min_value=1), min_length=1, max_length=20)
    category = serializers.ChoiceField(choices=["floor-plans", "amenities"])


class MediaActionsInput(ConfirmedInput):
    action = serializers.ChoiceField(choices=[
        "rename", "set-active", "remove", "restore", "normalize", "reset-crop",
        "segment", "evaluate-amenities", "reprocess-all", "reprocess-failed",
    ])
    targets = MediaTarget(many=True, max_length=200)
    name = serializers.CharField(required=False, allow_blank=True, max_length=160, trim_whitespace=False)
    active = serializers.BooleanField(required=False)
    rotationDegrees = serializers.ChoiceField(choices=[0, 90, 180, 270], required=False)
    reason = serializers.CharField(required=False, max_length=500)

    def validate(self, attrs):
        action = attrs["action"]
        bulk = action in {"evaluate-amenities", "reprocess-all", "reprocess-failed"}
        if bool(attrs["targets"]) == bulk:
            raise serializers.ValidationError("Bulk actions require empty targets; plan actions require targets.")
        if len({item["planId"] for item in attrs["targets"]}) != len(attrs["targets"]):
            raise serializers.ValidationError("Select each plan once.")
        for key, allowed in {"name": "rename", "active": "set-active", "rotationDegrees": "normalize"}.items():
            if (key in attrs) != (action == allowed):
                raise serializers.ValidationError({key: f"This field is required only for {allowed}."})
        return attrs


class PictureInput(StrictInput):
    requestId = serializers.UUIDField()
    expectedUpdatedAt = serializers.DateTimeField()
    source = serializers.ChoiceField(choices=["asset", "automatic", "upload"])
    assetId = serializers.UUIDField(required=False)
    image = serializers.FileField(required=False)

    def validate(self, attrs):
        if ("assetId" in attrs) != (attrs["source"] == "asset"):
            raise serializers.ValidationError("Choose an asset only for an existing-image cover.")
        if ("image" in attrs) != (attrs["source"] == "upload"):
            raise serializers.ValidationError("Choose an image file only for an uploaded cover.")
        return attrs


class BuilderMediaView(BuilderView):
    def get(self, request, project_id):
        from .project_workspace import admin_media_tools
        project = scoped_project(request.user, project_id, workspace=self.workspace)
        editable = project.status not in {"retired", "merged"} and (
            self.workspace != "admin" or request.user.has_perm("platform_api.change_floorplan")
        )
        can_crop = editable and is_enabled("media.crop_drafts", actor=request.user, project=project)
        can_evaluate = editable and is_enabled("media.extraction_queue", actor=request.user, project=project)
        project_plans = list(FloorPlan.objects.filter(project_id__in=project_evidence_ids(project), document__project_id__in=project_evidence_ids(project)).order_by("pk"))
        evaluations = layout_evaluation_states(project_plans)
        inactive_pages = {(plan.document_id, plan.page_number) for plan in project_plans
                          if plan.plan_scope == "floor" and not plan.active and plan.review_state not in HIDDEN_REVIEW_STATES}
        plans = {}
        for plan in project_plans:
            plans.setdefault(plan.asset_id, []).append(plan)
        base = f"{self.api_prefix}/builder/projects/{project.pk}/media"
        items = []
        cover_document = None if project.project_pic else first_picture_document(project)
        if project.project_pic or cover_document:
            items.append({
                "id": "project-picture", "category": "project-images", "label": "Project picture",
                "imageUrl": f"{base}/picture" if project.project_pic else
                    f"{base}/documents/{cover_document.pk}/pages/1?sha256={cover_document.sha256}",
                "documentId": str(cover_document.pk) if cover_document else "", "pageNumber": 1,
                "planId": None, "assetId": None, "updatedAt": None,
                "active": True, "reviewState": "", "displayName": "", "canEdit": False,
                "sourceSha256": cover_document.sha256 if cover_document else "",
                "canCrop": bool(can_crop and cover_document), "canEvaluate": False,
            })
        for asset in private_assets(project).select_related("document").order_by("created_at", "pk"):
            linked = plans.get(asset.pk, [None])
            for plan in linked:
                category = asset.source_bounds.get("category", "")
                if plan:
                    category = "layouts" if plan.plan_scope == "flat" else "floor-plans"
                if category not in {"layouts", "floor-plans", "amenities", "project-images"}:
                    category = "project-images"
                items.append({
                    "id": str(plan.pk) if plan else str(asset.pk), "assetId": str(asset.pk), "category": category,
                    "label": plan.display_title if plan else f"{asset.document.original_name} - page {asset.page_number}",
                    "imageUrl": f"{base}/assets/{asset.pk}", "documentId": str(asset.document_id),
                    "pageNumber": asset.page_number, "planId": str(plan.pk) if plan else None,
                    "updatedAt": plan.updated_at if plan else None, "sourceSha256": asset.document.sha256,
                    "active": plan.active if plan else True, "inactiveSource": bool(plan and plan.plan_scope != "floor" and (plan.document_id, plan.page_number) in inactive_pages), "reviewState": plan.review_state if plan else "",
                    "displayName": plan.display_name if plan else "", "canEdit": bool(editable and plan),
                    "configuration": plan.configuration_type if plan else "",
                    "area": plan.carpet_area_sqft if plan else None,
                    "dimensions": calculate_room_areas(plan.dimensions) if plan else [],
                    "roomRectangleSumSqFt": room_rectangle_sum_sqft(plan.dimensions) if plan else None,
                    "roomCount": plan.room_count if plan else None,
                    "bathrooms": plan.bathroom_count if plan else None,
                    "balconies": plan.balcony_count if plan else None,
                    "orientation": plan.orientation if plan else "",
                    "confidence": plan.confidence if plan else None,
                    "extractorVersion": plan.extractor_version if plan else "",
                    "sourceKind": asset.asset_type,
                    "qualityWarning": plan.raw_extraction.get("qualityWarning", "") if plan else "",
                    "dimensionWarning": plan.raw_extraction.get("dimensionWarning", "") if plan else "",
                    "features": plan.features if plan else {},
                    "canReview": bool(self.workspace == "admin" and editable and plan and
                        (request.user.is_superuser or not project.organization.memberships.filter(user=request.user, status="active").exists())),
                    "reviewReason": "A different administrator must review your organization's evidence.",
                    "evaluation": evaluations.get(str(plan.pk)) if plan else None,
                    "adminTools": admin_media_tools(request.user, project, plan) if self.workspace == "admin" and plan else [],
                    "canCrop": bool(can_crop and asset.document.declared_media_type in SOURCE_TYPES
                                    and (not plan or (plan.active and plan.review_state not in HIDDEN_REVIEW_STATES))),
                    "canEvaluate": bool(can_evaluate and plan and plan.plan_scope == "flat" and plan.active
                                        and plan.review_state not in HIDDEN_REVIEW_STATES and not plan.crop_remaining),
                })
        if request.query_params.get("sort") in {"name", "name-desc"}:
            import re
            def natural_name(item):
                return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", item["label"])]
            items.sort(key=natural_name, reverse=request.query_params["sort"] == "name-desc")
        items.sort(key=lambda item: item["active"] is False)
        documents = []
        for document in EvidenceDocument.objects.filter(project_id__in=project_evidence_ids(project), scan_status="clean", declared_media_type__in=SOURCE_TYPES):
            try:
                count = source_page_count(document)
            except (ValueError, OSError):
                # A broken source must be visible, not silently absent from the workspace.
                documents.append({"id": str(document.pk), "name": document.original_name,
                                  "pageCount": 0, "sha256": document.sha256,
                                  "error": "Source unavailable or integrity check failed. Contact the project administrator."})
            else:
                documents.append({"id": str(document.pk), "name": document.original_name,
                                  "pageCount": count, "sha256": document.sha256})
        live = set(QueueWorker.objects.filter(project=project, state__in=["starting", "running"],
            heartbeat_at__gte=timezone.now() - timedelta(seconds=LIVE_SECONDS)).values_list("message_id", flat=True))
        jobs = [{"id": str(job.pk), "kind": job.event_type, "error": job.last_error_code,
                 "planId": job.payload.get("floorPlanId"),
                 "state": "completed" if job.published_at else "dead_letter" if job.dead_lettered_at else
                 "running" if job.pk in live else "retrying" if job.available_after and job.available_after > timezone.now() else "queued"}
                for job in project_jobs(project).order_by("-occurred_at")[:50]]
        return Response({
            "projectId": str(project.pk), "projectName": project.submitted_name,
            "adminTools": admin_media_tools(request.user, project) if self.workspace == "admin" else [],
            "projectPrice": project_price_label(project), "canEdit": editable,
            "confirmNameChanges": project.organization.confirm_name_changes,
            "projectUpdatedAt": project.updated_at.isoformat(),
            "items": items, "documents": documents,
            "profiles": [{"id": item["id"], "label": item["label"]} for item in list_ai_floor_plan_vision_profiles()["profiles"]],
            "drafts": [{"id": str(draft.pk), "label": draft.label, "imageUrl": f"{base}/drafts/{draft.pk}",
                        "action": draft.bounds.get("draftAction", "new"),
                        "category": draft.bounds.get("category", "layouts"),
                        "targetPlanId": str(draft.source_plan_id) if draft.bounds.get("draftAction") == "update" else None}
                       for draft in LayoutCropDraft.objects.filter(project=project, owner=request.user).order_by("created_at")],
            "jobs": jobs,
        })


class BuilderAssetView(BuilderView):
    def get(self, request, project_id, asset_id):
        project = scoped_project(request.user, project_id, workspace=self.workspace)
        asset = get_object_or_404(private_assets(project), pk=asset_id)
        if not storage.exists(asset.object_key):
            raise NotFound("This media file is unavailable.")
        response = FileResponse(storage.open(asset.object_key, "rb"), content_type=asset.media_type)
        response["X-Content-Type-Options"] = "nosniff"
        return response


class BuilderPictureView(BuilderView):
    def post(self, request, project_id):
        return BuilderPictureAction(workspace=self.workspace).post(request, project_id)

    def get(self, request, project_id):
        project = scoped_project(request.user, project_id, workspace=self.workspace)
        if not project.project_pic:
            raise NotFound("This project picture is unavailable.")
        try:
            picture = project.project_pic.open("rb")
        except OSError as exc:
            raise NotFound("This project picture is unavailable.") from exc
        response = FileResponse(picture, content_type="image/png")
        response["X-Content-Type-Options"] = "nosniff"
        return response


class BuilderPageView(BuilderView):
    def get(self, request, project_id, document_id, page_number):
        project = scoped_project(request.user, project_id, workspace=self.workspace)
        document = scoped_document(project, document_id, request.query_params.get("sha256"))
        try:
            image = render_source_pages(document, [page_number])[0][1]
        except OSError as exc:
            raise NotFound("This source file is unavailable.") from exc
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return HttpResponse(image, content_type="image/png")


class BuilderDraftPreviewView(BuilderView):
    def get(self, request, project_id, draft_id):
        project = scoped_project(request.user, project_id, workspace=self.workspace)
        draft = get_object_or_404(LayoutCropDraft, project=project, owner=request.user, pk=draft_id)
        image = bytes(draft.image)
        if hashlib.sha256(image).hexdigest() != draft.sha256:
            raise NotFound("Draft checksum mismatch.")
        response = HttpResponse(image, content_type="image/png")
        response["X-Content-Type-Options"] = "nosniff"
        return response


class BuilderMediaAction(BuilderView):
    serializer_class = ConfirmedInput
    action_name = ""

    def fingerprint(self, values):
        return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()

    def prepare(self, project, actor, values):
        return None

    def acquire_locks(self, project, actor, values, locks):
        ids = [target["planId"] for target in values.get("targets", [])]
        ids += [values[key] for key in ("planId", "targetPlanId") if key in values]
        ids += values.get("replacementIds", [])
        documents = set()
        if values.get("documentId"):
            documents.add(get_object_or_404(EvidenceDocument, project_id__in=project_evidence_ids(project), pk=values["documentId"]).pk)
        for draft_id in values.get("draftIds", []):
            draft = LayoutCropDraft.objects.filter(project=project, owner=actor, pk=draft_id).first()
            if draft is None:
                raise ValueError("Select your saved drafts from this project.")
            if draft.document_id:
                documents.add(get_object_or_404(EvidenceDocument, project_id__in=project_evidence_ids(project), pk=draft.document_id).pk)
            if draft.source_plan_id:
                ids.append(draft.source_plan_id)
        for plan_id in set(ids):
            documents.add(scoped_plan(project, plan_id).document_id)
        if values.get("action") in {"evaluate-amenities", "reprocess-all", "reprocess-failed"}:
            documents.update(EvidenceDocument.objects.filter(project_id__in=project_evidence_ids(project)).values_list("pk", flat=True))
        for document_id in sorted(documents, key=str):
            locks.enter_context(work_lock("document", document_id))
        if values.get("draftIds") or self.action_name == "crops":
            locks.enter_context(work_lock("crop-cart", actor.pk))
        for job_id in OutboxMessage.objects.filter(
            payload__floorPlanId__in=[str(pk) for pk in ids], published_at__isnull=True,
            dead_lettered_at__isnull=True,
        ).order_by("pk").values_list("pk", flat=True):
            locks.enter_context(work_lock("outbox", job_id))

    def post(self, request, project_id):
        project = scoped_project(request.user, project_id, edit=True, workspace=self.workspace)
        data = self.serializer_class(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        scope = f"builder-media:{project.pk}:{self.action_name}"
        key = str(values["requestId"])
        fingerprint = self.fingerprint(values)
        self.saved_keys = []
        try:
            # Commit jobs, review invalidation and replay records together: a worker
            # must never see a builder edit before publication is invalidated.
            with work_lock("builder-media", project.pk), ExitStack() as locks:
                project = scoped_project(request.user, project_id, edit=True, workspace=self.workspace)
                existing = IdempotencyRecord.objects.filter(user=request.user, scope=scope, key=key).first()
                if existing:
                    if existing.response_body["fingerprint"] != fingerprint:
                        raise BuilderConflict("This request ID was already used for another action. Refresh and try again.")
                    return Response(existing.response_body["result"], status=existing.response_status)
                self.acquire_locks(project, request.user, values, locks)
                self.prepared = self.prepare(project, request.user, values)
                with transaction.atomic():
                    project = get_object_or_404(builder_projects(request.user, workspace=self.workspace).select_for_update(of=("self",)), pk=project.pk)
                    result = self.perform(project, request.user, values)
                    if self.action_name != "crops" and values.get("action") != "discard":
                        current = Project.objects.select_for_update().get(pk=project.pk)
                        require_new_review(current)
                        current.save(update_fields=["status", "updated_at"])
                    IdempotencyRecord.objects.create(user=request.user, scope=scope, key=key,
                                                     response_status=202,
                                                     response_body={"fingerprint": fingerprint, "result": result})
                    audit(request, f"builder.media_{self.action_name}", project)
                return Response(result, status=202)
        except WorkAlreadyClaimed as exc:
            raise BuilderConflict("Media is being processed. Wait for it to finish, then retry this action.") from exc
        except project_media_edits.edits.StaleCropEdit as exc:
            raise BuilderConflict(str(exc)) from exc
        except DjangoValidationError as exc:
            raise ValidationError({"detail": exc.messages}) from exc
        except ValueError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        except OSError as exc:
            raise ValidationError({"detail": "The source image is unavailable. Refresh media before retrying."}) from exc
        finally:
            for key in self.saved_keys:
                if not project.floor_plan_assets.filter(object_key=key).exists() and not Project.objects.filter(pk=project.pk, project_pic=key).exists():
                    storage.delete(key)


class BuilderCropView(BuilderMediaAction):
    serializer_class = CropInput
    action_name = "crops"

    def prepare(self, project, actor, values):
        scoped_document(project, values["documentId"], values["sourceSha256"])
        if "targetPlanId" not in values:
            if values["category"] == "layouts":
                from .domain.geometry import validate_regions

                regions = validate_regions(values["regions"], allow_overlap=True)
                if any(region.width * region.height + 1e-15 < 0.02 for region in regions):
                    raise ValueError("Each new layout crop must cover at least 2% of the source image area.")
            return None
        plan = scoped_plan(project, values["targetPlanId"])
        check_version(plan, values["expectedUpdatedAt"])
        category = "floor-plans" if plan.plan_scope == "floor" else "layouts"
        if (plan.document_id != values["documentId"] or plan.page_number != values["pageNumber"]
                or values["category"] != category):
            raise ValidationError("Crop update must use this plan's original page and category.")
        return project_media_edits.prepare_revision(
            plan, project, actor, regions=values["regions"], rotation=values["rotationDegrees"], source_mode=values["sourceMode"],
        )

    def perform(self, project, actor, values):
        scoped_document(project, values["documentId"], values["sourceSha256"])
        if "targetPlanId" in values:
            plan = scoped_plan(project, values["targetPlanId"], lock=True)
            check_version(plan, values["expectedUpdatedAt"])
            draft = project_media_edits.stage_revision(plan, project, actor, self.prepared)
            return {"state": "draft", "draftIds": [str(draft.pk)], "planIds": [], "queued": []}
        rotation = values["rotationDegrees"]
        result = stage_page_crops(project, values["documentId"], values["pageNumber"],
                                  values["regions"], actor, values["sourceSha256"], -90 if rotation == 270 else rotation)
        for draft in LayoutCropDraft.objects.filter(pk__in=result["draftIds"], project=project, owner=actor):
            draft.bounds = draft.bounds | {"category": values["category"]}
            draft.save(update_fields=["bounds", "updated_at"])
        return result


class BuilderDraftActionView(BuilderMediaAction):
    serializer_class = DraftInput
    action_name = "drafts"

    def prepare(self, project, actor, values):
        if values["action"] == "discard":
            return {}
        prepared = {}
        drafts = list(LayoutCropDraft.objects.filter(project=project, owner=actor, pk__in=values["draftIds"]))
        if len(drafts) != len(values["draftIds"]):
            raise ValueError("Select your saved drafts from this project.")
        updates = [draft for draft in drafts if draft.bounds.get("draftAction") == "update"]
        if len({draft.source_plan_id for draft in updates}) != len(updates):
            raise ValueError("Commit only one update per plan at a time.")
        for draft in drafts:
            action = draft.bounds.get("draftAction")
            if action not in {None, "page", "update"}:
                raise ValueError("Select new crops or existing-plan crop updates.")
            if action == "page":
                document = scoped_document(project, draft.document_id, draft.source_sha256)
                image = render_source_pages(document, [draft.page_number])[0][1]
                if hashlib.sha256(image).hexdigest() != draft.sha256 or hashlib.sha256(bytes(draft.image)).hexdigest() != draft.sha256:
                    raise ValueError("Saved page changed; discard it and select the source again.")
                prepared[str(draft.pk)] = {"sourcePage": True}
            if action == "update":
                plan = scoped_plan(project, draft.source_plan_id)
                prepared[str(draft.pk)] = project_media_edits.prepare_saved_revision(draft, plan, project, actor)
        if values.get("replacementIds"):
            if any(draft.bounds.get("draftAction") == "page" for draft in drafts):
                raise ValueError("Attach saved pages separately from layout replacements.")
            if any(draft.bounds.get("category", "layouts") != "layouts" for draft in drafts if draft not in updates):
                raise ValueError("Only new individual layouts can replace selected layouts.")
            if len(updates) == len(drafts) or set(values["replacementIds"]) & {draft.source_plan_id for draft in updates}:
                raise ValueError("Do not replace a plan being updated; select new layout drafts for replacement.")
            for plan_id in values["replacementIds"]:
                plan = scoped_plan(project, plan_id)
                expected = next(item["expectedUpdatedAt"] for item in values["replacementTargets"] if item["planId"] == plan.pk)
                check_version(plan, expected)
                if plan.plan_scope != "flat" or plan.review_state in HIDDEN_REVIEW_STATES:
                    raise ValueError("Select visible individual layouts from this project to replace.")
        return prepared

    def perform(self, project, actor, values):
        if values["action"] == "discard":
            discard_drafts(project, actor, values["draftIds"])
            return {"state": "discarded"}
        for target in values.get("replacementTargets", []):
            check_version(scoped_plan(project, target["planId"], lock=True), target["expectedUpdatedAt"])
        updates = []
        for draft_id, prepared in self.prepared.items():
            draft = get_object_or_404(LayoutCropDraft, project=project, owner=actor, pk=draft_id)
            if prepared.get("sourcePage"):
                add_source_pages(project, draft.document_id, [draft.page_number], draft.bounds["category"], actor,
                    values.get("visionProfileId") or draft.bounds.get("visionProfileId"),
                    expected_source_sha256=draft.source_sha256, expected_page_sha256={draft.page_number: draft.sha256})
                draft.delete()
                continue
            plan = scoped_plan(project, draft.source_plan_id, lock=True)
            project_media_edits.commit_revision(plan, project, actor, prepared, self.saved_keys,
                                               values.get("visionProfileId"))
            updates.append(str(plan.pk))
            draft.delete()
        new_ids = [pk for pk in values["draftIds"] if str(pk) not in self.prepared]
        plans = attach_drafts(project, actor, new_ids, replacement_ids=values.get("replacementIds", []),
                              vision_profile_id=values.get("visionProfileId")) if new_ids else []
        plans += updates
        return {"state": "queued", "planIds": plans}


class BuilderEvaluateView(BuilderMediaAction):
    serializer_class = EvaluationInput
    action_name = "evaluate"

    def perform(self, project, actor, values):
        require_enabled("media.extraction_queue", actor=actor, project=project)
        with transaction.atomic():
            plan = scoped_plan(project, values["planId"], lock=True)
            if not plan.active or plan.plan_scope != "flat" or plan.review_state in HIDDEN_REVIEW_STATES:
                raise NotFound("Choose an active individual layout from this project.")
            if plan.updated_at != values["expectedUpdatedAt"]:
                raise BuilderConflict("This layout changed. Refresh media before re-evaluating.")
            message, operation = queue_layout_evaluation(
                plan, actor=actor, vision_profile_id=values.get("visionProfileId"), retry=True, force=True, fresh=values["fresh"],
            )
            if plan.review_state == "approved":
                plan.review_state = "needs_review"
                plan.save(update_fields=["review_state", "updated_at"])
        return {"state": operation.state, "operationId": str(operation.pk), "jobId": str(message.pk)}


class BuilderPagesView(BuilderMediaAction):
    serializer_class = PagesInput
    action_name = "pages"

    def perform(self, project, actor, values):
        scoped_document(project, values["documentId"], values["sourceSha256"])
        result = add_source_pages(project, values["documentId"], values["pages"], values["category"],
                                  actor, values.get("visionProfileId"), expected_source_sha256=values["sourceSha256"])
        return {"state": "queued", "pages": result}


class BuilderPictureAction(BuilderMediaAction):
    serializer_class = PictureInput
    action_name = "picture"

    def fingerprint(self, values):
        content = dict(values)
        image = content.pop("image", None)
        if image is not None:
            if image.size > 10 * 1024 * 1024:
                raise ValidationError("Choose an image up to 10 MB.")
            self.upload = image.read(10 * 1024 * 1024 + 1)
            content["imageSha256"] = hashlib.sha256(self.upload).hexdigest()
        return super().fingerprint(content)

    def prepare(self, project, actor, values):
        from .project_pictures import extracted_picture, normalize_picture
        check_version(project, values["expectedUpdatedAt"])
        if values["source"] == "upload":
            return normalize_picture(self.upload)
        if values["source"] == "automatic":
            return None
        get_object_or_404(private_assets(project), pk=values["assetId"])
        return extracted_picture(project, values["assetId"]).read()

    def perform(self, project, actor, values):
        check_version(project, values["expectedUpdatedAt"])
        if values["source"] == "automatic":
            project.project_pic = ""
        else:
            project.project_pic.save(f"project-{uuid4()}.png", ContentFile(self.prepared), save=False)
            self.saved_keys.append(project.project_pic.name)
        project.save(update_fields=["project_pic", "updated_at"])
        from .models import AuditEvent
        AuditEvent.objects.create(actor=actor, action="project.picture_changed", entity_type="Project",
            entity_id=project.pk, correlation_id=uuid4(), metadata={"source": values["source"]})
        return {"state": "saved", "imageUrl": f"{self.api_prefix}/builder/projects/{project.pk}/media/picture" if project.project_pic else None}


class BuilderActionsView(BuilderMediaAction):
    serializer_class = MediaActionsInput
    action_name = "actions"

    def prepare(self, project, actor, values):
        prepared = {}
        for target in values["targets"]:
            plan = scoped_plan(project, target["planId"])
            check_version(plan, target["expectedUpdatedAt"])
            if values["action"] in {"normalize", "reset-crop"}:
                prepared[str(plan.pk)] = project_media_edits.prepare_revision(
                    plan, project, actor, normalize=values["action"] == "normalize",
                    rotation=values.get("rotationDegrees", 0),
                    regions=[{"x": 0, "y": 0, "width": 1, "height": 1}],
                )
        return prepared

    def perform(self, project, actor, values):
        from .floor_plan_layout_jobs import queue_floor_plan_layouts
        from .floor_plan_activity import set_plan_active_locked
        from .floor_plan_removal import set_floor_plan_removed
        from .layout_names import save_layout_name
        from .reanalysis import queue_document_reanalysis
        from .source_page_additions import _queue_page
        from .floor_plan_vision import resolve_ai_vision_selection

        action = values["action"]
        targets = [scoped_plan(project, target["planId"], lock=True) for target in values["targets"]]
        for plan, target in zip(targets, values["targets"]):
            check_version(plan, target["expectedUpdatedAt"])
        jobs, plans = [], []
        for plan in targets:
            if action == "rename":
                save_layout_name(project, plan.pk, values["name"], actor)
            elif action == "set-active":
                set_plan_active_locked(plan, values["active"], actor, require_review=True)
            elif action in {"remove", "restore"}:
                plan, _ = set_floor_plan_removed(project, plan.pk, actor, removed=action == "remove",
                                                expected_updated_at=plan.updated_at)
                if action == "restore" and plan.review_state == "approved":
                    plan.review_state = "needs_review"
                    plan.save(update_fields=["review_state", "updated_at"])
            elif action in {"normalize", "reset-crop"}:
                project_media_edits.commit_revision(plan, project, actor, self.prepared[str(plan.pk)],
                                                   self.saved_keys, values.get("visionProfileId"))
            elif action == "segment":
                if not plan.active:
                    raise ValueError("Activate this floor plan before extracting individual layouts.")
                require_enabled("media.extraction_queue", actor=actor, project=project)
                try:
                    job = queue_floor_plan_layouts(plan, actor, vision_profile_id=values.get("visionProfileId"), retry=True, fresh=values["fresh"])
                except ValueError as exc:
                    reasons = {
                        "STALE_VISION_SELECTION": "This source is pinned to AI settings that have changed. Select a current profile.",
                        "SOURCE_ASSET_NOT_READY": "The source document must pass the clean-source check before extraction.",
                        "FULL_FLOOR_SOURCE_REQUIRED": "Choose a full-floor source, not an individual layout crop.",
                        "Feature is disabled.": "Check the media.auto_segment feature flag.",
                    }
                    raise ValueError(reasons.get(str(exc), "Check source readiness and the configured AI profile.")) from exc
                jobs.append(str(job.pk))
            if action not in {"remove", "restore"}:
                FloorPlan.objects.filter(pk=plan.pk, review_state="approved").update(review_state="needs_review")
            plans.append(str(plan.pk))
        if action.startswith("reprocess-"):
            require_enabled("media.extraction_queue", actor=actor, project=project)
            documents = EvidenceDocument.objects.filter(project_id__in=project_evidence_ids(project)).order_by("pk")
            if action == "reprocess-failed":
                documents = documents.filter(status="extraction_failed")
            else:
                from .reanalysis import RETRYABLE_DOCUMENT_STATUSES
                documents = documents.filter(status__in=RETRYABLE_DOCUMENT_STATUSES)
            for document in documents:
                job = queue_document_reanalysis(document.pk, actor, reason_code="PROJECT_WORKSPACE_REANALYSIS",
                                               vision_profile_id=values.get("visionProfileId"), allow_merged=True)
                if job:
                    jobs.append(str(job[0].pk))
        elif action == "evaluate-amenities":
            require_enabled("media.source_pages", actor=actor, project=project)
            require_enabled("media.extraction_queue", actor=actor, project=project)
            selection = resolve_ai_vision_selection(values.get("visionProfileId"))
            for asset in private_assets(project).filter(source_bounds__category="amenities").select_related(
                "document", "workflow_run"
            ):
                if asset.document.sha256 != asset.source_bounds.get("sourceSha256"):
                    raise BuilderConflict("Amenity source changed; refresh before re-evaluating.")
                with storage.open(asset.object_key, "rb") as stream:
                    image = stream.read(asset.byte_length + 1)
                if len(image) != asset.byte_length or hashlib.sha256(image).hexdigest() != asset.sha256:
                    raise ValueError("Amenity image checksum mismatch.")
                jobs.append(str(_queue_page(asset, "amenities", actor, selection, retry=True, force=True).pk))
        return {"state": "queued" if jobs else "saved", "jobIds": jobs, "planIds": plans}
