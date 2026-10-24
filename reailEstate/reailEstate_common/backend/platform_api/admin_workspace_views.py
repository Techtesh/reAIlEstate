"""Custom workspace readers and reviewed mutations; never render admin templates."""
import hashlib
import io

from django import forms
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .builder_views import BuilderConflict, BuilderView
from .builder_media import scoped_project
from .models import EvidenceDocument, FloorPlan, Project, ProjectAmenity, ReviewTask
from .services import audit
from .api_common import AdminAPIView


class WorkspaceDocumentView(BuilderView):
    def get(self, request, project_id, document_id):
        from .artifact_storage import storage
        from .infrastructure.source_documents import SOURCE_TYPES, source_page_count
        project = scoped_project(request.user, project_id, workspace=self.workspace)
        from .project_workspace import project_evidence_ids
        document = get_object_or_404(EvidenceDocument, pk=document_id, project_id__in=project_evidence_ids(project))
        if document.scan_status != "clean":
            raise PermissionDenied("Preview is available after a clean malware scan.")
        if request.query_params.get("metadata") == "1":
            pages, error = 0, ""
            if document.declared_media_type in SOURCE_TYPES:
                try:
                    pages = source_page_count(document)
                except (ValueError, OSError):
                    error = "Original preview is unavailable or failed its integrity check."
            else:
                error = "No page preview is available for this format. Open/download the original instead."
            return Response({"pageCount": pages, "previewError": error, "mediaType": document.declared_media_type, "sha256": document.sha256})
        try:
            with storage.open(document.object_key, "rb") as stream:
                data = stream.read(document.byte_length + 1)
        except OSError:
            raise Http404("Original document is unavailable.") from None
        if len(data) != document.byte_length or hashlib.sha256(data).hexdigest() != document.sha256:
            raise Http404("Document integrity check failed.")
        inline = document.declared_media_type in SOURCE_TYPES
        response = FileResponse(io.BytesIO(data), content_type=document.declared_media_type if inline else "application/octet-stream",
                                as_attachment=not inline, filename=document.original_name)
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


def require_permission(user, model, action="view"):
    permitted = user.has_perm(f"platform_api.{action}_{model}") or (action == "view" and user.has_perm(f"platform_api.change_{model}"))
    if not user.is_authenticated or not user.is_active or not user.is_staff or not permitted:
        raise PermissionDenied("Your account does not have permission for this action.")


def independent(user, project, submitted_by=None):
    if not user.is_superuser and (submitted_by == user.pk or project.organization.memberships.filter(user=user, status="active").exists()):
        raise PermissionDenied("A different administrator must review your organization's evidence.")


class ProjectReviewView(AdminAPIView):
    permission_classes = [IsAdminUser]

    def handle_exception(self, exc):
        from .ingestion_runtime import WorkAlreadyClaimed
        if isinstance(exc, WorkAlreadyClaimed):
            exc = BuilderConflict("This source is busy. Refresh before reviewing it.")
        elif isinstance(exc, DjangoValidationError):
            exc = ValidationError(exc.messages)
        elif isinstance(exc, ValueError):
            exc = ValidationError(str(exc))
        return super().handle_exception(exc)

    def get(self, request, project_id):
        require_permission(request.user, "project")
        project = get_object_or_404(Project, pk=project_id)
        from .external_sources_admin import location_reviewer_allowed
        member = not request.user.is_superuser and project.organization.memberships.filter(user=request.user, status="active").exists()
        amenities = []
        if request.user.has_perm("platform_api.view_projectamenity") or request.user.has_perm("platform_api.change_projectamenity"):
            for item in project.project_amenities.select_related("amenity"):
                own = any(row.get("submittedBy") == request.user.pk for row in item.evidence_summary.get("manual", []))
                allowed = request.user.has_perm("platform_api.change_projectamenity") and (request.user.is_superuser or not (member or own))
                amenities.append({"id": str(item.pk), "name": item.amenity.display_name, "state": item.evidence_summary.get("reviewState", "unreviewed"),
                    "availability_state": item.availability_state, "distance_meters": item.distance_meters, "travel_minutes": item.travel_minutes,
                    "evidence": item.evidence_summary, "updatedAt": item.updated_at, "canReview": allowed,
                    "reason": "" if allowed else "Independent review or additional amenity permission is required."})
        references = []
        if request.user.has_perm("platform_api.view_reviewtask") or request.user.has_perm("platform_api.change_reviewtask"):
            for item in project.review_tasks.filter(task_type="external_sources").order_by("-created_at")[:100]:
                allowed = request.user.has_perm("platform_api.change_reviewtask") and location_reviewer_allowed(item, request.user)
                references.append({"id": str(item.pk), "version": item.version, "state": item.resolution.get("reviewState", "unreviewed"),
                    "evidence": item.resolution, "canReview": allowed,
                    "reason": "" if allowed else "Independent source review or review-task permission is required."})
        return Response({"projectVersion": project.updated_at, "amenities": amenities, "references": references})

    @transaction.atomic
    def post(self, request, project_id):
        require_permission(request.user, "project", "change")
        project = get_object_or_404(Project.objects.select_for_update(), pk=project_id)
        if project.status in {"retired", "merged"}:
            raise ValidationError("Retired/merged projects are read-only.")
        reason = serializers.CharField(max_length=1000).run_validation(request.data.get("reason"))
        decision = serializers.ChoiceField(choices=["accept", "reject"]).run_validation(request.data.get("decision"))
        if request.data.get("kind") == "amenity":
            require_permission(request.user, "projectamenity", "change")
            item = get_object_or_404(ProjectAmenity.objects.select_for_update(), project=project, pk=request.data.get("id"))
            expected = serializers.DateTimeField().run_validation(request.data.get("expectedUpdatedAt"))
            if expected != item.updated_at:
                raise BuilderConflict("This amenity changed. Reload before reviewing it.")
            if decision == "accept":
                independent(request.user, project)
                if not request.user.is_superuser and any(row.get("submittedBy") == request.user.pk for row in item.evidence_summary.get("manual", [])):
                    raise PermissionDenied("A different administrator must review your amenity claim.")
            class AmenityInput(serializers.Serializer):
                availability_state = serializers.CharField(max_length=32)
                distance_meters = serializers.IntegerField(min_value=0, allow_null=True)
                travel_minutes = serializers.IntegerField(min_value=0, allow_null=True)
            values = AmenityInput(data={name: request.data.get(name, getattr(item, name)) for name in AmenityInput().fields})
            values.is_valid(raise_exception=True)
            for name, value in values.validated_data.items():
                setattr(item, name, value)
            item.evidence_summary = {**item.evidence_summary, "reviewState": "accepted" if decision == "accept" else "rejected",
                "reviewedBy": request.user.pk, "reviewedAt": timezone.now().isoformat(), "reviewReason": reason}
            item.save()
            audit(request, "project.amenity_reviewed", item, metadata={"projectId": str(project.pk), "reason": reason})
        elif request.data.get("kind") == "reference":
            require_permission(request.user, "reviewtask", "change")
            from .external_sources_admin import apply_manual_reference, apply_google_reference, location_reviewer_allowed
            item = get_object_or_404(ReviewTask.objects.select_for_update(), project=project, pk=request.data.get("id"), task_type="external_sources")
            if request.data.get("expectedVersion") != item.version:
                raise BuilderConflict("This reference changed. Reload before reviewing it.")
            if not location_reviewer_allowed(item, request.user):
                raise PermissionDenied("Independent source review is required.")
            if item.state != "open":
                raise BuilderConflict("This reference has already been resolved.")
            if decision == "accept" and item.resolution.get("manualLocation"):
                apply_manual_reference(project, request.user, item.pk,
                    serializers.DateTimeField().run_validation(request.data.get("projectVersion")), item.version)
            elif decision == "accept" and item.resolution.get("googlePlaceId"):
                apply_google_reference(project, request.user, item.pk)
            else:
                item.resolution = {**item.resolution, "reviewState": "accepted" if decision == "accept" else "rejected",
                    "reviewedBy": request.user.pk, "reviewReason": reason, "reviewedAt": timezone.now().isoformat()}
                item.state = "resolved"; item.version += 1; item.completed_at = timezone.now(); item.save()
            audit(request, "project.source_reviewed", item, metadata={"reason": reason, "decision": decision})
        else:
            raise ValidationError("Choose an amenity or source reference.")
        return Response({"status": "reviewed"})


class PlanReviewView(AdminAPIView):
    permission_classes = [IsAdminUser]

    def handle_exception(self, exc):
        from .ingestion_runtime import WorkAlreadyClaimed
        if isinstance(exc, WorkAlreadyClaimed):
            exc = BuilderConflict("This plan has pending evaluation work. Refresh before editing it.")
        elif isinstance(exc, DjangoValidationError):
            exc = ValidationError(exc.messages)
        elif isinstance(exc, ValueError):
            exc = ValidationError(str(exc))
        return super().handle_exception(exc)

    def post(self, request, plan_id):
        serializers.ChoiceField(choices=["approve", "reject", "save-correction"]).run_validation(request.data.get("decision"))
        require_permission(request.user, "floorplan", "change")
        plan = get_object_or_404(FloorPlan.objects.select_related("project", "document"), pk=plan_id)
        if plan.updated_at != serializers.DateTimeField().run_validation(request.data.get("expectedUpdatedAt")):
            raise BuilderConflict("This plan changed. Reload before reviewing it.")
        if not plan.active or plan.review_state == "removed_by_user" or plan.project.status in {"retired", "merged"} or plan.document.scan_status != "clean":
            raise ValidationError("Review requires an active plan and a clean source document.")
        reason = serializers.CharField(max_length=1000).run_validation(request.data.get("reason"))
        if plan.plan_scope == "floor":
            from .floor_evaluation_review import review_floor_evaluation
            return Response(review_floor_evaluation(request, plan, reason))
        if plan.plan_scope != "flat":
            raise ValidationError("Classify this plan as a whole floor or individual layout before reviewing it.")
        if request.data.get("scope") == "floor" or "floorEvaluation" in request.data:
            raise ValidationError("Floor evaluation fields cannot be applied to an individual layout.")
        if request.data.get("decision") == "save-correction":
            from .layout_extraction_admin import ExtractionForm, RoomFormSet, save_extraction
            values = request.data.get("fields", {})
            if not isinstance(values, dict):
                raise ValidationError("Expected extraction fields.")
            form = ExtractionForm({**values, "expected_updated_at": plan.updated_at, "reason": reason})
            rows = request.data.get("rooms", [])
            if not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(row, dict) for row in rows):
                raise ValidationError("Expected at most 100 room rows.")
            payload = {"rooms-TOTAL_FORMS": len(rows), "rooms-INITIAL_FORMS": len(plan.dimensions), "rooms-MIN_NUM_FORMS": 0, "rooms-MAX_NUM_FORMS": 100}
            for index, row in enumerate(rows):
                payload.update({f"rooms-{index}-{key}": value for key, value in row.items()})
            rooms = RoomFormSet(payload, prefix="rooms")
            valid_fields, valid_rooms = form.is_valid(), rooms.is_valid()
            if not valid_fields or not valid_rooms:
                messages = [f"{form.fields[name].label or name}: {error}" for name, errors in form.errors.items()
                    for error in errors if name in form.fields]
                messages += [f"Room {index + 1}, {RoomFormSet.form.base_fields[name].label or name}: {error}"
                    for index, row_errors in enumerate(rooms.errors) for name, field_errors in row_errors.items()
                    for error in field_errors if name in RoomFormSet.form.base_fields]
                messages += [str(error) for error in rooms.non_form_errors()]
                raise ValidationError({"detail": " ".join(messages[:8]) or "Review the highlighted extraction fields.",
                    "fields": form.errors, "rooms": rooms.errors})
            try:
                save_extraction(plan.pk, request.user, form.cleaned_data, rooms)
            except ValueError as exc:
                raise BuilderConflict(str(exc)) from None
            return Response({"status": "saved_for_review"})
        independent(request.user, plan.project)
        latest_correction = ReviewTask.objects.filter(project=plan.project, resolution__floorPlanId=str(plan.pk),
            resolution__source="manual_extraction").order_by("-created_at").first()
        if latest_correction and not request.user.is_superuser and latest_correction.resolution.get("submittedBy") == request.user.pk:
            raise PermissionDenied("A different administrator must review your extraction correction.")
        # Approval is distinct from editing; reuse the established fact-set/audit workflow.
        from .views import ResolveFloorPlanView
        response = ResolveFloorPlanView().post(request, floor_plan_id=plan.pk)
        audit(request, "floor_plan.review_reason", plan, metadata={"reason": reason})
        return response
