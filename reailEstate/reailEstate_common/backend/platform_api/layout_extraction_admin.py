from decimal import Decimal
from copy import deepcopy
from uuid import uuid4

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import models, transaction
from django.http import Http404, HttpResponseNotAllowed, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone

from .ingestion_runtime import WorkAlreadyClaimed, work_lock
from .jobs.events import EXTRACTION_EVENTS
from .layout_crop_update import editable_layout
from .models import AuditEvent, EvidenceDocument, FloorPlan, OutboxMessage, ReviewTask
from .domain.room_dimensions import calculate_room_areas, dimension_warning


EXTRACTION_FIELDS = (
    "configuration_type", "carpet_area_sqft", "room_count", "bathroom_count", "balcony_count", "orientation",
)


class ExtractionForm(forms.Form):
    expected_updated_at = forms.DateTimeField(widget=forms.HiddenInput)
    configuration_type = forms.CharField(max_length=32, required=False)
    carpet_area_sqft = forms.DecimalField(label="Carpet area (sq ft)", required=False,
        min_value=Decimal("0.01"), max_digits=10, decimal_places=2)
    room_count = forms.IntegerField(label="Bedroom count", required=False, min_value=0, max_value=100)
    bathroom_count = forms.IntegerField(required=False, min_value=0, max_value=100)
    balcony_count = forms.IntegerField(required=False, min_value=0, max_value=100)
    orientation = forms.CharField(required=False, max_length=32)
    reason = forms.CharField(label="Correction reason", max_length=1000, widget=forms.Textarea(attrs={"rows": 3}))


class RoomForm(forms.Form):
    source_index = forms.IntegerField(required=False, min_value=0, widget=forms.HiddenInput)
    room = forms.CharField(label="Room", max_length=120)
    # Extracted feet/inches conversions have floating-point tails; accept saved precision unchanged.
    length_ft = forms.FloatField(label="Length (ft)", required=False, min_value=0.001, max_value=1000)
    width_ft = forms.FloatField(label="Width (ft)", required=False, min_value=0.001, max_value=1000)


RoomFormSet = forms.formset_factory(RoomForm, extra=1, can_delete=True, max_num=100, validate_max=True,
    absolute_max=100)


def room_initial(plan):
    if not isinstance(plan.dimensions, list) or any(not isinstance(row, dict) for row in plan.dimensions):
        raise ValueError("Room data is malformed; the original extraction has been preserved.")
    return [{"source_index": index, "room": row.get("room") or row.get("name", ""),
             "length_ft": row.get("lengthFt"), "width_ft": row.get("widthFt")}
            for index, row in enumerate(plan.dimensions)]


def corrected_dimensions(plan, rooms):
    dimensions, seen = [], set()
    for room_form in rooms:
        data = room_form.cleaned_data
        if not data:
            continue
        index = data.get("source_index")
        if index is not None:
            if index >= len(plan.dimensions) or index in seen:
                raise ValueError("Room rows changed. Reload the editor before saving.")
            seen.add(index)
        if data.get("DELETE"):
            continue
        row = dict(plan.dimensions[index]) if index is not None else {}
        row.update(room=data["room"], lengthFt=float(data["length_ft"]) if data["length_ft"] is not None else None,
                   widthFt=float(data["width_ft"]) if data["width_ft"] is not None else None)
        dimensions.append(row)
    if seen != set(range(len(plan.dimensions))):
        raise ValueError("Room rows are missing. Reload the editor before saving.")
    return calculate_room_areas(dimensions)


def extraction_values(plan):
    return {name: str(value) if isinstance(value, Decimal) else value
            for name in (*EXTRACTION_FIELDS, "dimensions") for value in [getattr(plan, name)]}


def corrected_dimension_metadata(plan, rooms):
    raw = deepcopy(plan.raw_extraction)
    if not raw.get("dimensionEvidence"):
        return raw
    evidence = {item["roomIndex"]: item for item in raw["dimensionEvidence"] if item.get("roomIndex") is not None}
    next_index = 0
    for form in rooms:
        data = form.cleaned_data
        if not data:
            continue
        index = data.get("source_index")
        item = evidence.get(index)
        if data.get("DELETE"):
            if item:
                item.update(resolved=True, roomIndex=None)
            continue
        if item:
            previous = plan.dimensions[index]
            changed_axes = [axis for axis in ("length", "width")
                            if previous.get(axis + "Ft") != (float(data[axis + "_ft"]) if data.get(axis + "_ft") is not None else None)]
            resolved = set(item.get("resolvedWarnings", []))
            corrected_axes = set(item.get("correctedAxes", [])) | set(changed_axes)
            for axis in changed_axes:
                resolved.update((f"AI-estimated {axis}", f"unknown or unusable {axis}/unit"))
            if corrected_axes == {"length", "width"}:
                resolved.update(("AI-inferred unit", "conflicting unit evidence"))
            item.setdefault("sourceRoom", item["room"])
            item.update(room=data["room"], roomIndex=next_index, resolvedWarnings=sorted(resolved), correctedAxes=sorted(corrected_axes))
        next_index += 1
    raw["dimensionWarning"] = dimension_warning(raw["dimensionEvidence"])
    return raw


def save_extraction(plan_id, actor, data, rooms):
    snapshot, destination = editable_layout(plan_id, actor)
    with work_lock("document", snapshot.document_id), transaction.atomic():
        EvidenceDocument.objects.select_for_update().get(pk=snapshot.document_id)
        locked = FloorPlan.objects.select_for_update().get(pk=plan_id)
        if locked.document_id != snapshot.document_id or locked.updated_at != data["expected_updated_at"]:
            raise ValueError("This layout changed. Reload the editor before saving.")
        current, destination = editable_layout(plan_id, actor)
        if OutboxMessage.objects.filter(event_type__in=EXTRACTION_EVENTS, published_at__isnull=True,
                dead_lettered_at__isnull=True).filter(
                models.Q(payload__floorPlanId=str(plan_id)) | models.Q(payload__documentId=str(current.document_id))).exists():
            raise WorkAlreadyClaimed("This layout has pending evaluation work.")
        room_initial(current)
        before = extraction_values(current)
        for name in EXTRACTION_FIELDS:
            setattr(current, name, data[name])
        dimensions = corrected_dimensions(current, rooms)
        current.raw_extraction = corrected_dimension_metadata(current, rooms)
        current.dimensions = dimensions
        after = extraction_values(current)
        changed_fields = [name for name in after if before[name] != after[name]]
        if not changed_fields:
            return current, False
        current.review_state = "needs_boundary_review" if current.raw_extraction.get("qualityWarning") else "needs_review"
        current.save(update_fields=[*EXTRACTION_FIELDS, "dimensions", "raw_extraction", "review_state", "updated_at"])
        task = ReviewTask.objects.create(project=destination, task_type="floor_plan", priority=1, resolution={
            "floorPlanId": str(current.pk), "source": "manual_extraction", "submittedBy": actor.pk,
            "reviewState": "unreviewed", "reason": data["reason"], "before": before, "after": after,
            "editedAt": timezone.now().isoformat(),
        })
        AuditEvent.objects.create(actor=actor, action="floor_plan.extraction_corrected", entity_type="FloorPlan",
            entity_id=current.pk, correlation_id=uuid4(), reason_code="MANUAL_EXTRACTION_CORRECTION",
            metadata={"projectId": str(destination.pk), "reviewTaskId": str(task.pk), "changedFields": changed_fields})
        return current, True


class LayoutExtractionAdminMixin:
    def layout_extraction_urls(self):
        return [path("<uuid:object_id>/edit-extraction/", self.admin_site.admin_view(self.edit_extraction_view),
                     name="platform_api_floorplan_edit_extraction")]

    def edit_extraction_view(self, request, object_id):
        if request.method not in {"GET", "POST"}:
            return HttpResponseNotAllowed(["GET", "POST"])
        if not self.has_change_permission(request):
            raise PermissionDenied
        try:
            plan, project = editable_layout(object_id, request.user)
            initial_rooms = room_initial(plan)
        except (FloorPlan.DoesNotExist, ValueError):
            raise Http404("An active, clean layout is required for extraction editing.") from None
        initial = {name: getattr(plan, name) for name in EXTRACTION_FIELDS}
        initial["expected_updated_at"] = plan.updated_at.isoformat()
        form = ExtractionForm(request.POST if request.method == "POST" else None, initial=initial)
        rooms = RoomFormSet(request.POST if request.method == "POST" else None, initial=initial_rooms, prefix="rooms")
        cancel_url = reverse("admin:platform_api_project_media_dashboard", args=[project.pk]) + (
            "?section=floor-plans" if plan.plan_scope == "floor" else "?section=layouts")
        status = 200
        if request.method == "POST":
            valid_form, valid_rooms = form.is_valid(), rooms.is_valid()
            if valid_form and valid_rooms:
                try:
                    plan, changed = save_extraction(plan.pk, request.user, form.cleaned_data, rooms)
                except ValueError as exc:
                    form.add_error(None, str(exc))
                    status = 409
                except WorkAlreadyClaimed:
                    form.add_error(None, "Evaluation work is pending or running. Nothing was changed; finish that work and reload.")
                    status = 409
                else:
                    messages.success(request, "Extraction correction saved for review. Published facts are unchanged."
                                     if changed else "No extraction values changed.")
                    return HttpResponseRedirect(cancel_url)
            else:
                status = 400
        response = TemplateResponse(request, "admin/platform_api/layout_extraction.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta, "original": plan,
            "title": f"Edit extraction: {plan.display_title}", "form": form, "rooms": rooms,
            "cancel_url": cancel_url, "image_url": reverse("floor-plan-asset-media", args=[plan.asset_id]),
        }, status=status)
        response["Cache-Control"] = "private, no-store"
        return response
