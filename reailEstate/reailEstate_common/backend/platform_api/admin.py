from copy import copy
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
import json
import re
from uuid import uuid4
from urllib.parse import urlencode

from django import forms
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.admin.options import IncorrectLookupParameters
from django.contrib.admin.views.main import ChangeList
from django.core.exceptions import SuspiciousOperation
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import models
from django.http import HttpResponseRedirect, QueryDict
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join

from .models import (
    Amenity,
    AuditEvent,
    Consent,
    DocumentLayoutPage,
    EvidenceDocument,
    FactCandidate,
    FeatureFlag,
    FloorPlan,
    FloorPlanAsset,
    IngestionRun,
    Organization,
    OrganizationMembership,
    Project,
    ProjectMerge,
    ProjectFact,
    RecommendationSnapshot,
    RequirementProfile,
    ReviewTask,
    StationCandidate,
)
from .floor_plan_segmentation_service import (
    FlatSeparationFailed,
    available_flat_separation_ai_profiles,
    separate_floor_plan_units,
)
from .layout_image_normalization import normalize_layout_image
from .layout_pages import validate_document_page
from .layout_reanalysis import create_manual_layout_crops
from .project_lifecycle import merge_projects, project_family_ids, retire_project
from .project_admin_actions import add_manual_amenities, project_retirement_response
from .reanalysis import queue_project_reanalysis
from .workers import process_pending_outbox
from .source_page_admin import SourcePageAdminMixin
from .external_sources_admin import (
    ExternalSourcesAdminMixin, GOOGLE_MATCH_LABELS, GoogleMatchFilter, MahaRERAFilter, google_match_annotations,
    LOCATION_REFERENCE_QUERY, manual_reference_details,
)
from .source_page_additions import PAGE_EVENT
from .floor_plan_layout_jobs import UNITS_EVENT, queue_floor_plan_layouts
from .crop_cart_admin import CropCartAdminMixin
from .models import LayoutCropDraft
from .floor_plan_removal import authorize_plan_removal, set_floor_plan_removed
from .floor_plan_visibility import REMOVED_REVIEW_STATE, visible_floor_plans, visible_plan_assets
from .ingestion_runtime import WorkAlreadyClaimed
from .layout_crop_update_admin import LayoutCropUpdateAdminMixin
from .layout_extraction_admin import EXTRACTION_FIELDS, LayoutExtractionAdminMixin
from .layout_bulk_admin import LayoutBulkAdminMixin
from .project_layout_health import (
    ProjectLayoutHealthAdminMixin, NeedsBoundaryReviewFilter,
    project_layout_health_context, project_boundary_layouts,
)


class BuilderMembershipInline(admin.TabularInline):
    model = OrganizationMembership
    fields = ("organization", "role", "named_role", "status")
    extra = 1
    verbose_name = "organization membership"
    verbose_name_plural = "Builder organization access (active owner/editor required)"


User = get_user_model()
admin.site.unregister(User)


@admin.register(User)
class WorkspaceUserAdmin(UserAdmin):
    inlines = (BuilderMembershipInline,)

    def get_inline_instances(self, request, obj=None):
        return super().get_inline_instances(request, obj) if obj else []


@admin.register(StationCandidate)
class StationCandidateAdmin(admin.ModelAdmin):
    list_display = ("name", "line", "kind", "latitude", "longitude", "needs_review", "active", "issue")
    list_filter = ("needs_review", "active", "kind", "line")
    search_fields = ("name", "line")
    readonly_fields = ("coordinate_options", "created_at", "updated_at")
    fields = ("name", "line", "kind", ("latitude", "longitude"), "sequence", "active", "needs_review",
              "issue", "coordinate_options", "created_at", "updated_at")


class PlanRemovalConfirmationForm(forms.Form):
    confirm = forms.BooleanField(label="Confirm this action for this record only")
    expected_updated_at = forms.DateTimeField(widget=forms.HiddenInput)


class LayoutPriceForm(forms.Form):
    floor_plan_id = forms.UUIDField(widget=forms.HiddenInput)
    expected_updated_at = forms.DateTimeField(widget=forms.HiddenInput)
    price = forms.DecimalField(
        label="Total price (INR)", required=False, min_value=Decimal("0.01"),
        max_digits=16, decimal_places=2,
        help_text="Leave blank for contact to know. Enter the total price, not a per-square-foot rate.",
    )


def project_price_label(project):
    from .layout_pricing import project_price_label as label
    return label(project)


def _asset_preview(asset):
    if asset is None:
        return "No saved image is associated with this plan."
    if asset.status != "ready":
        return "The saved image is not ready."
    url = reverse("floor-plan-asset-media", kwargs={"asset_id": asset.id})
    return format_html(
        '<a href="{}" target="_blank" rel="noopener">'
        '<img src="{}" alt="Saved plan image, source page {}" '
        'style="max-width:100%;max-height:700px;height:auto;display:block" />'
        'Open full-resolution image</a>',
        url, url, asset.page_number or "unknown",
    )


class ImageNavigationAdminMixin:
    change_form_template = "admin/platform_api/image_change_form.html"

    def change_view(self, request, object_id, form_url="", extra_context=None):
        context = dict(extra_context or {})
        context["image_navigation"] = self.record_navigation(request, self.get_object(request, object_id))
        return super().change_view(request, object_id, form_url, context)

    def record_navigation(self, request, obj, view_name=None):
        # Rebuild the originating list rather than navigating every project's images.
        list_request = copy(request)
        list_request.GET = QueryDict(request.GET.get("_changelist_filters", ""))
        navigation = None
        if obj is not None and self.has_view_or_change_permission(request, obj):
            try:
                changelist = self.get_changelist_instance(list_request)
                ids = [str(item.pk) for item in changelist.queryset
                       if self.has_view_or_change_permission(request, item)]
                current = str(obj.pk)
                if current in ids:
                    index = ids.index(current)
                    filters = list_request.GET.urlencode()
                    suffix = "?" + urlencode({"_changelist_filters": filters}) if filters else ""
                    opts = self.model._meta
                    def change_url(pk):
                        return reverse(view_name or f"admin:{opts.app_label}_{opts.model_name}_change",
                                       args=[pk], current_app=self.admin_site.name) + suffix
                    navigation = {
                        "previous": change_url(ids[index - 1]) if index else None,
                        "next": change_url(ids[index + 1]) if index + 1 < len(ids) else None,
                        "position": index + 1,
                        "total": len(ids),
                        "list": reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist",
                                        current_app=self.admin_site.name) + ("?" + filters if filters else ""),
                    }
            except (IncorrectLookupParameters, SuspiciousOperation):
                # Invalid preserved filters must not silently broaden navigation scope.
                navigation = None
        return navigation


class ProjectPageSizeChangeList(ChangeList):
    def __init__(self, request, *args, **kwargs):
        arguments = list(args)
        requested = request.GET.get("page_size", "20")
        self.project_page_size = requested if requested in {"10", "20", "50", "all"} else "20"
        if self.project_page_size in {"10", "20", "50"}:
            arguments[7] = int(self.project_page_size)
        elif self.project_page_size == "all":
            arguments[7] = max(1, arguments[10].get_queryset(request).count())
        clean_request = copy(request)
        clean_request.GET = request.GET.copy()
        clean_request.GET.pop("page_size", None)
        super().__init__(clean_request, *arguments, **kwargs)

    def get_query_string(self, new_params=None, remove=None):
        query = super().get_query_string(new_params, remove)
        separator = "&" if query else "?"
        return f"{query}{separator}page_size={self.project_page_size}"


class FlatSeparationForm(forms.Form):
    mode = forms.ChoiceField(choices=[("script", "Script"), ("ai", "AI"), ("both", "Script and AI")])
    expected_count = forms.IntegerField(
        required=False, min_value=1, max_value=40,
        help_text="Optional quality gate. It never forces the script or AI to invent flats.",
    )
    vision_profile_id = forms.ChoiceField(required=False, label="AI vision profile")
    supersede_source = forms.BooleanField(
        required=False,
        help_text="Only after every selected method succeeds, mark the full-floor record as superseded.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            catalog = available_flat_separation_ai_profiles()
            self.profile_catalog_error = False
        except ValueError:
            catalog = {"profiles": []}
            self.profile_catalog_error = True
        self.fields["vision_profile_id"].choices = [
            ("", "Configured default")
        ] + [(item["id"], f'{item["label"]} — {item["model"]}') for item in catalog["profiles"]]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("mode") == "script":
            cleaned["vision_profile_id"] = None
        elif self.profile_catalog_error:
            self.add_error("vision_profile_id", "The AI vision profile catalog is invalid.")
        elif not available_flat_separation_ai_profiles()["profiles"]:
            self.add_error("vision_profile_id", "No enabled AI vision profile is configured.")
        return cleaned


class ManualCropForm(forms.Form):
    regions_json = forms.CharField(widget=forms.HiddenInput)
    rotation_degrees = forms.IntegerField(widget=forms.HiddenInput, initial=0)
    queue_evaluation = forms.BooleanField(required=False, initial=True)
    vision_profile_id = forms.ChoiceField(required=False, label="AI vision profile")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            profiles = available_flat_separation_ai_profiles()["profiles"]
        except ValueError:
            profiles = []
        self.fields["vision_profile_id"].choices = [("", "Configured default")] + [
            (item["id"], f'{item["label"]} — {item["model"]}') for item in profiles
        ]

    def clean_regions_json(self):
        try:
            regions = json.loads(self.cleaned_data["regions_json"])
        except (TypeError, ValueError):
            raise forms.ValidationError("Draw at least one valid crop rectangle.") from None
        if not isinstance(regions, list) or not regions:
            raise forms.ValidationError("Draw at least one crop rectangle.")
        return regions

    def clean_rotation_degrees(self):
        value = self.cleaned_data["rotation_degrees"]
        if value not in {-180, -90, 0, 90, 180}:
            raise forms.ValidationError("Invalid rotation.")
        return value


class ProjectMergeForm(forms.Form):
    target_project = forms.ModelChoiceField(queryset=Project.objects.none())
    reason_code = forms.CharField(max_length=64, initial="DUPLICATE_PROJECT")
    notes = forms.CharField(max_length=1000, required=False, widget=forms.Textarea)

    def __init__(self, source, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target_project"].queryset = Project.objects.filter(
            organization=source.organization
        ).exclude(id=source.id).exclude(status__in=["merged", "retired"]).order_by("submitted_name")


class PlanSegmentationMethodFilter(admin.SimpleListFilter):
    title = "segmentation method"
    parameter_name = "segmentation_method"

    def lookups(self, request, model_admin):
        return [("source", "Source"), ("script", "Script"), ("ai", "AI")]

    def queryset(self, request, queryset):
        if self.value() == "source":
            return queryset.exclude(raw_extraction__has_key="segmentationMethod")
        if self.value() in {"script", "ai"}:
            return queryset.filter(raw_extraction__segmentationMethod=self.value())
        return queryset


@admin.register(FloorPlan)
class FloorPlanAdmin(LayoutExtractionAdminMixin, LayoutCropUpdateAdminMixin, ImageNavigationAdminMixin, admin.ModelAdmin):
    list_display = ("plan_name", "project", "page_number", "record_level", "segmentation_method",
                    "configuration_type", "price", "active", "crop_remaining", "review_state")
    list_filter = ("active", "crop_remaining", "review_state", "plan_scope", "asset__asset_type", PlanSegmentationMethodFilter)
    search_fields = ("project__submitted_name", "plan_name", "document__original_name")
    list_select_related = ("project", "document", "asset")
    readonly_fields = ("saved_image", "extraction_notice", "removal_action", "review_state", "display_name", "price", "pricing_editor",
                       *EXTRACTION_FIELDS, "dimensions", "features", "raw_extraction", "confidence", "extractor_version")
    ordering = ("project__submitted_name", "document_id", "page_number", "plan_name", "id")

    @admin.display(description="Layout pricing")
    def pricing_editor(self, obj):
        if not obj or not obj.pk or obj.plan_scope != "flat":
            return "Pricing is entered on individual layouts."
        project_id = ProjectMerge.objects.filter(source_project_id=obj.project_id, state="applied").values_list(
            "target_project_id", flat=True
        ).first() or obj.project_id
        url = reverse("admin:platform_api_project_media_dashboard", args=[project_id])
        return format_html('<a href="{}?section=layouts">Edit total price in the layout dashboard</a>', url)

    def has_delete_permission(self, request, obj=None):
        # Removal is reversible; the default admin delete would destroy evidence.
        return False

    def save_model(self, request, obj, form, change):
        previous_active = (
            FloorPlan.objects.filter(pk=obj.pk).values_list("active", flat=True).first()
            if change and obj.pk else None
        )
        super().save_model(request, obj, form, change)
        if previous_active is not None and previous_active != obj.active:
            from django.db import transaction

            from .floor_plan_activity import set_plan_active_locked

            with transaction.atomic():
                locked = FloorPlan.objects.select_for_update().get(pk=obj.pk)
                set_plan_active_locked(locked, obj.active, request.user)

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        if obj and obj.review_state == REMOVED_REVIEW_STATE:
            return tuple(dict.fromkeys((*fields, *(field.name for field in self.model._meta.fields))))
        return fields

    @admin.display(description="Remove / restore record")
    def removal_action(self, obj):
        if not obj or not obj.pk:
            return "Save the record first."
        action = "restore" if obj.review_state == REMOVED_REVIEW_STATE else "remove"
        project_id = ProjectMerge.objects.filter(source_project_id=obj.project_id, state="applied").values_list(
            "target_project_id", flat=True
        ).first() or obj.project_id
        url = reverse("admin:platform_api_floorplan_visibility", args=[obj.pk, project_id, action],
                      current_app=self.admin_site.name)
        return format_html('<a class="button" href="{}">{}</a>', url,
                           "Restore removed plan…" if action == "restore" else "Remove duplicate plan…")

    def visibility_view(self, request, object_id, project_id, action):
        from django.core.exceptions import PermissionDenied
        from django.http import Http404, HttpResponseNotAllowed

        if action not in {"remove", "restore"}:
            raise Http404
        if request.method not in {"GET", "POST"}:
            return HttpResponseNotAllowed(["GET", "POST"])
        project_admin = self.admin_site._registry[Project]
        project = project_admin.get_object(request, str(project_id))
        obj = self.get_object(request, str(object_id))
        if project is None or obj is None or obj.project_id not in project_family_ids(project):
            raise Http404
        if not project_admin.has_view_or_change_permission(request, project) or not self.has_change_permission(request, obj):
            raise PermissionDenied
        authorize_plan_removal(request.user, project)
        form = PlanRemovalConfirmationForm(
            request.POST if request.method == "POST" else None,
            initial={"expected_updated_at": obj.updated_at.isoformat()},
        )
        record_url = reverse("admin:platform_api_floorplan_change", args=[obj.pk], current_app=self.admin_site.name)
        dashboard_url = reverse("admin:platform_api_project_media_dashboard", args=[project.pk],
                                current_app=self.admin_site.name) + (
                                    "?section=floor-plans" if obj.plan_scope == "floor" else "?section=layouts")
        if request.method == "POST" and form.is_valid():
            try:
                obj, changed = set_floor_plan_removed(
                    project, obj.pk, request.user, removed=action == "remove",
                    expected_updated_at=form.cleaned_data["expected_updated_at"],
                )
            except FloorPlan.DoesNotExist:
                raise Http404 from None
            except WorkAlreadyClaimed:
                form.add_error(None, "This source or task is busy. Nothing was changed; retry after it finishes.")
            except ValueError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, ("Plan removed. Restore it from its floor-plan admin record."
                                          if action == "remove" else "Plan restored. Cancelled jobs were not restarted.")
                                 if changed else "This action was already applied; no further changes were made.")
                return HttpResponseRedirect(dashboard_url if action == "remove" else record_url)
        request.current_app = self.admin_site.name
        return TemplateResponse(request, "admin/platform_api/floor_plan_removal_confirmation.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta, "original": obj,
            "title": "Remove duplicate floor plan" if action == "remove" else "Restore removed floor plan",
            "form": form, "action": action, "dashboard_project": project,
            "record_url": record_url, "cancel_url": dashboard_url if action == "remove" else record_url,
        })

    def get_urls(self):
        opts = self.model._meta
        custom = self.layout_extraction_urls() + self.layout_crop_update_urls() + [
            path(
                "<uuid:object_id>/visibility/<uuid:project_id>/<str:action>/",
                self.admin_site.admin_view(self.visibility_view),
                name=f"{opts.app_label}_{opts.model_name}_visibility",
            ),
            path(
                "<path:object_id>/segment-units/",
                self.admin_site.admin_view(self.segment_units_view),
                name=f"{opts.app_label}_{opts.model_name}_segment_units",
            ),
            path(
                "<str:object_id>/manual-crops/",
                self.admin_site.admin_view(self.manual_crops_view),
                name=f"{opts.app_label}_{opts.model_name}_manual_crops",
            ),
            path(
                "<str:object_id>/manual-crops/change/",
                self.admin_site.admin_view(self.manual_crops_view),
                name=f"{opts.app_label}_{opts.model_name}_manual_crops_legacy",
            ),
        ]
        return custom + super().get_urls()

    def change_view(self, request, object_id, form_url="", extra_context=None):
        context = dict(extra_context or {})
        obj = self.get_object(request, object_id)
        if obj and obj.review_state != REMOVED_REVIEW_STATE and self.has_change_permission(request, obj):
            context["extraction_editor_url"] = reverse("admin:platform_api_floorplan_edit_extraction", args=[obj.pk])
        if obj and obj.review_state != REMOVED_REVIEW_STATE and self.has_change_permission(request, obj) and obj.asset_id \
                and obj.asset.asset_type != "unit_crop" and obj.asset.status == "ready" \
                and obj.document.scan_status == "clean":
            context["flat_separation_url"] = reverse(
                "admin:platform_api_floorplan_segment_units", args=[obj.pk],
                current_app=self.admin_site.name,
            )
            context["manual_crop_url"] = reverse(
                "admin:platform_api_floorplan_manual_crops", args=[obj.pk],
                current_app=self.admin_site.name,
            )
        return super().change_view(request, object_id, form_url, context)

    def manual_crops_view(self, request, object_id):
        from .crop_cart import crop_destination
        obj = self.get_object(request, object_id)
        if obj is None or obj.review_state == REMOVED_REVIEW_STATE:
            from django.http import Http404
            raise Http404
        if not self.has_change_permission(request, obj):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        try:
            destination = crop_destination(obj.project)
        except ValueError as exc:
            from django.http import HttpResponseBadRequest
            return HttpResponseBadRequest(str(exc))
        if not self.admin_site._registry[Project].has_change_permission(request, destination):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        if obj.raw_extraction.get("boundingBox", {}).get("sourceDocumentId"):
            # Page-crop drafts may have source evidence without a full-page FloorPlan.
            return HttpResponseRedirect(reverse("admin:platform_api_project_add_source_pages", args=[destination.pk])
                + "?" + urlencode({"category": "layouts", "document": obj.document_id, "page_number": obj.page_number}))
        source_plan = self._manual_crop_source(obj)
        if source_plan is None or not source_plan.asset_id:
            messages.error(request, "No saved source page is available for this layout.")
            return HttpResponseRedirect(reverse("admin:platform_api_floorplan_change", args=[obj.pk]))
        obj = source_plan
        if (crop_destination(obj.project).pk != destination.pk
                or obj.document.project_id not in project_family_ids(destination)):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        form = ManualCropForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                result = create_manual_layout_crops(
                    obj, form.cleaned_data["regions_json"], actor=request.user,
                    vision_profile_id=form.cleaned_data["vision_profile_id"] or None,
                    queue=form.cleaned_data["queue_evaluation"],
                    rotation_degrees=form.cleaned_data["rotation_degrees"],
                )
            except WorkAlreadyClaimed:
                form.add_error(None, "The source PDF/document or your crop cart is being processed. "
                    "Nothing was saved. Your crop selections and rotation are still here; "
                    "try Save to cart again shortly.")
            except ValueError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f"Saved {len(result['draftIds'])} draft crop(s) to your cart ({result['cartCount']}/20). Select drafts and Add to project when ready. Existing layouts are unchanged.")
                return HttpResponseRedirect(reverse(
                    "admin:platform_api_project_media_dashboard", args=[result["projectId"]],
                    current_app=self.admin_site.name,
                ) + "?section=layouts&sort=unit#draft-crop-cart")
        context = {
            **self.admin_site.each_context(request), "opts": self.model._meta, "original": obj,
            "title": "Draw individual layout crops", "form": form, "destination_project": destination,
            "cart_count": LayoutCropDraft.objects.filter(owner=request.user).count(),
            "media_url": reverse("floor-plan-asset-media", kwargs={"asset_id": obj.asset_id}),
            "source_url": reverse("admin:platform_api_project_source_document", args=[
                destination.pk, obj.document_id,
            ]) + f"#page={obj.page_number or 1}",
            "cancel_url": reverse(
                "admin:platform_api_project_media_dashboard", args=[destination.pk],
                current_app=self.admin_site.name,
            ) + "?section=layouts",
        }
        return TemplateResponse(request, "admin/platform_api/floor_plan_manual_crops.html", context)

    @staticmethod
    def _manual_crop_source(plan):
        """Resolve any layout card back to its best saved original PDF-page image."""
        if not plan.asset_id:
            return None
        source_asset_id = plan.asset.source_bounds.get("sourceAssetId")
        candidates = FloorPlan.objects.select_related("asset", "document").filter(
            document_id=plan.document_id,
            page_number=plan.page_number,
            asset__isnull=False,
        ).exclude(review_state__in=["removed_by_user", "replaced_by_user"])
        if source_asset_id:
            source = candidates.filter(asset_id=source_asset_id).order_by("created_at").first()
            if source:
                return source
        source = candidates.exclude(asset__asset_type="unit_crop").order_by("created_at").first()
        return source or plan

    def segment_units_view(self, request, object_id):
        obj = self.get_object(request, object_id)
        if obj is None or obj.review_state == REMOVED_REVIEW_STATE:
            from django.http import Http404
            raise Http404
        if not self.has_change_permission(request, obj):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        form = FlatSeparationForm(request.POST or None)
        result = None
        if request.method == "POST" and form.is_valid():
            try:
                result = separate_floor_plan_units(
                    obj.id, mode=form.cleaned_data["mode"],
                    expected_count=form.cleaned_data["expected_count"],
                    vision_profile_id=form.cleaned_data["vision_profile_id"] or None,
                    supersede_source=form.cleaned_data["supersede_source"], actor=request.user,
                )
            except FlatSeparationFailed as exc:
                result = exc.result
                form.add_error(None, "No selected separation method completed. Review the method error codes below.")
            except ValueError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, "Flat separation completed; generated crops are awaiting review.")
                return HttpResponseRedirect(reverse(
                    "admin:platform_api_floorplan_change", args=[obj.pk], current_app=self.admin_site.name,
                ))
        context = {
            **self.admin_site.each_context(request), "opts": self.model._meta, "original": obj,
            "title": "Separate flats", "form": form, "result": result,
            "media_url": reverse("floor-plan-asset-media", kwargs={"asset_id": obj.asset_id}) if obj.asset_id else None,
        }
        return TemplateResponse(request, "admin/platform_api/floor_plan_segmentation.html", context)

    @admin.display(description="Saved plan image")
    def saved_image(self, obj):
        return _asset_preview(obj.asset if obj and obj.asset_id else None)

    @admin.display(description="Level")
    def record_level(self, obj):
        return "Individual house" if obj.asset_id and obj.asset.asset_type == "unit_crop" else "Full floor / source"

    @admin.display(description="Method")
    def segmentation_method(self, obj):
        return obj.raw_extraction.get("segmentationMethod", "source")

    @admin.display(description="Extraction status")
    def extraction_notice(self, obj):
        if obj and isinstance(obj.raw_extraction, dict) and obj.raw_extraction.get("visionProvider") == "rules":
            return "Identified locally without AI. This is a page-level candidate, not a verified or segmented flat layout. Review the saved image; unreadable or ambiguous measurements remain blank."
        if obj and isinstance(obj.raw_extraction, dict) and obj.raw_extraction.get("requiresVision"):
            return "Image saved, but visual extraction is pending. Configure a vision profile and reprocess the document; blank fields are not extracted values."
        return "Extracted values require review against the source image."


class AssetCategoryFilter(admin.SimpleListFilter):
    title = "image category"
    parameter_name = "category"

    def lookups(self, request, model_admin):
        return [("floor-plans", "Floor plans"), ("layouts", "Flat layouts"),
                ("general", "Design / general"), ("amenities", "Amenities"),
                ("unclassified", "Unclassified candidates"), ("legacy", "Legacy (no category)")]

    def queryset(self, request, queryset):
        if self.value() == "legacy":
            return queryset.exclude(source_bounds__has_key="category")
        if self.value() in {"floor-plans", "layouts", "general", "amenities", "unclassified"}:
            return queryset.filter(source_bounds__category=self.value())
        return queryset


class AssetAIGenerationFilter(admin.SimpleListFilter):
    title = "generation mode"
    parameter_name = "generation"

    def lookups(self, request, model_admin):
        return [("ai", "AI generated — needs review"), ("script", "Script generated")]

    def queryset(self, request, queryset):
        if self.value() == "ai":
            return queryset.filter(floor_plans__review_state="gen_with_ai_needs_review").distinct()
        if self.value() == "script":
            return queryset.filter(workflow_run__result__visionProvider="rules")
        return queryset


@admin.register(FloorPlanAsset)
class FloorPlanAssetAdmin(ImageNavigationAdminMixin, admin.ModelAdmin):
    list_display = ("id", "project", "page_number", "image_category", "record_level",
                    "segmentation_method", "source_asset_link", "storage_folder", "status", "created_at")
    search_fields = ("project__submitted_name", "document__original_name", "source_bounds__sourceAssetId")
    list_filter = (AssetCategoryFilter, AssetAIGenerationFilter, ("project", admin.RelatedOnlyFieldListFilter), "status", "asset_type")
    list_select_related = ("project", "document")
    readonly_fields = ("saved_image",)
    ordering = ("-created_at",)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        context = dict(extra_context or {})
        obj = self.get_object(request, object_id)
        if obj and obj.asset_type == "unit_crop" and self.has_view_or_change_permission(request, obj):
            context["layout_dashboard_url"] = reverse(
                "admin:platform_api_project_media_dashboard", args=[obj.project_id],
                current_app=self.admin_site.name,
            ) + "?section=layouts"
        return super().change_view(request, object_id, form_url, context)

    @admin.display(description="Image category")
    def image_category(self, obj):
        return obj.source_bounds.get("category", "legacy")

    @admin.display(description="Level")
    def record_level(self, obj):
        return "Individual house" if obj.asset_type == "unit_crop" else "Full floor / source"

    @admin.display(description="Method")
    def segmentation_method(self, obj):
        return obj.source_bounds.get("segmentationMethod", "source")

    @admin.display(description="Source floor")
    def source_asset_link(self, obj):
        source_id = obj.source_bounds.get("sourceAssetId")
        if not source_id:
            return "—"
        url = reverse("admin:platform_api_floorplanasset_change", args=[source_id])
        return format_html('<a href="{}">{}</a>', url, str(source_id)[:8])

    @admin.display(description="Folder")
    def storage_folder(self, obj):
        if obj.asset_type == "unit_crop":
            return f"{obj.project_id}/layouts/{obj.source_bounds.get('sourceAssetId')}/{obj.source_bounds.get('segmentationMethod')}"
        return f"{obj.project_id}/{obj.source_bounds.get('category', 'legacy')}/source"

    @admin.display(description="Saved plan image")
    def saved_image(self, obj):
        return _asset_preview(obj)


@admin.register(EvidenceDocument)
class EvidenceDocumentAdmin(admin.ModelAdmin):
    list_display = ("original_name", "project", "status", "scan_status", "error_category", "updated_at")
    list_filter = ("status", "scan_status", "error_category")
    search_fields = ("original_name", "project__submitted_name", "sha256")
    readonly_fields = ("object_key", "sha256", "byte_length", "detected_media_type")
    actions = ("retry_failed_extractions",)

    @admin.action(description="Retry selected failed extractions")
    def retry_failed_extractions(self, request, queryset):
        count = 0
        for document in queryset.filter(status="extraction_failed"):
            try:
                from .reanalysis import queue_document_reanalysis
                result = queue_document_reanalysis(document.id, request.user, "FAILED_EXTRACTION_RETRY")
            except ValueError:
                continue
            count += bool(result)
        self.message_user(request, f"Queued {count} failed document(s) for retry.")


class ProjectPictureWidget(forms.ClearableFileInput):
    def format_value(self, value):
        if value and getattr(value, "instance", None):
            return SimpleNamespace(name="Project pic", url=reverse("project-picture", args=[value.instance.pk]))
        return None


class ProjectAdminForm(forms.ModelForm):

    project_picture_source = forms.ChoiceField(
        label="Project pic from documents", required=False, widget=forms.RadioSelect,
        help_text="Choose an extracted image or use the first clean PDF page. An uploaded picture overrides the automatic default.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .project_pictures import picture_candidates

        choices = [("", "Keep current picture (first PDF page if none)"),
                   ("automatic", "Automatic: first page of the first clean PDF")]
        if self.instance.pk and not self.instance._state.adding:
            for asset in picture_candidates(self.instance):
                choices.append((str(asset.pk), format_html(
                    '<span class="project-picture-option"><img src="{}" alt="" loading="lazy" width="160" height="110">'
                    '<span>{} · Page {} · {}</span></span>',
                    reverse("floor-plan-asset-media", args=[asset.pk]), asset.document.original_name,
                    asset.page_number or "?", asset.source_bounds.get("category", "Image"))))
        self.fields["project_picture_source"].choices = choices

    def clean(self):
        data = super().clean()
        source = data.get("project_picture_source")
        if not source:
            return data
        if "project_pic" in self.changed_data:
            raise forms.ValidationError("Choose either an upload/clear action or a document picture, not both.")
        if source == "automatic":
            data["project_pic"] = False
        else:
            from .project_pictures import extracted_picture

            try:
                data["project_pic"] = extracted_picture(self.instance, source)
            except (ValueError, OSError) as exc:
                self.add_error("project_picture_source", str(exc))
        return data

    class Meta:
        model = Project
        fields = "__all__"
        widgets = {"project_pic": ProjectPictureWidget}

    def clean_project_pic(self):
        picture = self.cleaned_data.get("project_pic")
        if not picture or "project_pic" not in self.changed_data:
            return picture
        from PIL import Image, ImageOps

        if picture.size > 10 * 1024 * 1024:
            raise forms.ValidationError("Project pic must be 10 MB or smaller.")
        try:
            picture.seek(0)
            with Image.open(picture) as image:
                if image.format not in {"JPEG", "PNG", "WEBP"} or image.width * image.height > 20_000_000:
                    raise forms.ValidationError("Choose a JPEG, PNG or WebP image up to 20 megapixels.")
                normalized = ImageOps.exif_transpose(image).convert("RGB")
                normalized.thumbnail((2400, 2400))
                normalized.info.clear()
                output = BytesIO()
                normalized.save(output, format="PNG")
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise forms.ValidationError("Choose a valid project image.") from exc
        return SimpleUploadedFile("project.png", output.getvalue(), content_type="image/png")


class ProjectMediaPictureForm(ProjectAdminForm):
    class Meta(ProjectAdminForm.Meta):
        fields = ("project_pic",)


@admin.register(Project)
class ProjectAdmin(ExternalSourcesAdminMixin, ProjectLayoutHealthAdminMixin, LayoutBulkAdminMixin, CropCartAdminMixin, SourcePageAdminMixin, ImageNavigationAdminMixin, admin.ModelAdmin):
    form = ProjectAdminForm
    change_form_template = "admin/platform_api/project_change_form.html"
    change_list_template = "admin/platform_api/project_change_list.html"
    list_display = ("submitted_name", "created_at", "maharera_reference", "google_maps_reference", "locality", "status", "boundary_review_link", "media_dashboard_link")
    list_filter = ("locality", GoogleMatchFilter, MahaRERAFilter, NeedsBoundaryReviewFilter)
    search_fields = ("submitted_name", "locality", "maha_rera_id")
    readonly_fields = ("extraction_outputs", "media_dashboard_link", "google_location_reference")
    fieldsets = ((None, {
        "classes": ("project-details-grid", "project-metadata"),
        "fields": ("organization", "status", "maha_rera_id", "submitted_name",
                   "published_fact_set_version", "verification_status", "processing_attested", "verification_checked_at"),
    }), (None, {
        "classes": ("project-details-grid", "project-resources"),
        "fields": ("google_location_reference", "media_dashboard_link", "extraction_outputs"),
    }), (None, {
        "classes": ("project-details-grid", "project-address"),
        "fields": ("address", "locality"),
    }))

    ordering = ("submitted_name", "id")
    actions = ("retire_selected_projects", "queue_selected_projects_for_reanalysis")

    @admin.display(description="Google location reference")
    def google_location_reference(self, obj):
        from .external_sources import map_link
        from .models import ReviewTask

        if not obj or not obj.pk:
            return "Save the project first."
        url = self.external_sources_url(obj)
        task = ReviewTask.objects.filter(LOCATION_REFERENCE_QUERY, project=obj,
            task_type="external_sources").order_by("-created_at", "-pk").first()
        if not task:
            return format_html('No saved reference. <a href="{}">External sources</a>', url)
        if task.resolution.get("manualLocation"):
            details = manual_reference_details(task.resolution["manualLocation"])
            if details:
                return format_html('<a href="{}" target="_blank" rel="noopener noreferrer">View pin on Google Maps</a>'
                    ' &middot; Manual / {} &middot; <a href="{}">External sources</a>',
                    details["sourceUrl"], task.resolution.get("reviewState", "unreviewed"), url)
            return format_html('Invalid manual reference. <a href="{}">External sources</a>', url)
        return format_html('<a href="{}" target="_blank" rel="noopener noreferrer">View on Google Maps</a>'
            ' &middot; {} &middot; <a href="{}">External sources</a>',
            map_link(task.resolution["googlePlaceId"], obj.submitted_name),
            task.resolution.get("reviewState", "unreviewed"), url)

    def get_queryset(self, request):
        queryset = super().get_queryset(request).exclude(address__has_key="mergedIntoProjectId").exclude(
            merge_out__state="applied"
        )
        if request.GET.get("show_inactive_organizations") != "true":
            queryset = queryset.filter(organization__status=Organization.Status.ACTIVE)
        return google_match_annotations(queryset).annotate(
            admin_preserved_filters=models.Value(self.get_preserved_filters(request), output_field=models.CharField()))

    def external_sources_url(self, obj):
        url = reverse("admin:platform_api_project_external_sources", args=[obj.pk])
        filters = getattr(obj, "admin_preserved_filters", "")
        return url + ("?" + filters if filters else "")

    @admin.display(description="Google Maps")
    def google_maps_reference(self, obj):
        url = self.external_sources_url(obj)
        if obj.manual_location:
            return format_html('<a href="{}">Manual / {}</a>', url, obj.google_review_state or "unreviewed")
        if obj.google_place_id:
            from .external_sources import map_link
            return format_html('<a href="{}">{}</a><br><a href="{}" target="_blank" rel="noopener noreferrer">Maps</a>',
                url, obj.google_review_state or "unreviewed", map_link(obj.google_place_id, obj.submitted_name))
        return format_html('<a href="{}">{}</a>', url, GOOGLE_MATCH_LABELS.get(obj.google_match_status, "No reference"))

    @admin.display(description="MahaRERA", ordering="maha_rera_id")
    def maharera_reference(self, obj):
        return format_html('<a href="{}">{}</a><br>{}',
            self.external_sources_url(obj),
            obj.maha_rera_id or "Missing ID", obj.verification_status)

    def has_delete_permission(self, request, obj=None):
        # The delete screen is retained as a confirmation surface, but delete_view
        # performs a safe retirement instead of cascading evidence/history.
        return self.has_change_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def delete_view(self, request, object_id, extra_context=None):
        project = self.get_object(request, object_id)
        if project is None:
            from django.http import Http404
            raise Http404
        if not self.has_delete_permission(request, project):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        return project_retirement_response(self.admin_site, request, project)

    @admin.action(description="Retire selected projects (safe delete)")
    def retire_selected_projects(self, request, queryset):
        completed = 0
        for project in queryset:
            try:
                retire_project(project.id, request.user)
            except ValueError as exc:
                self.message_user(request, f"Could not retire {project}: {exc}", level=messages.ERROR)
            else:
                completed += 1
        self.message_user(request, f"Retired {completed} project(s); evidence and audit history were preserved.")

    @admin.action(description="Add selected projects to reanalysis queue")
    def queue_selected_projects_for_reanalysis(self, request, queryset):
        count = sum(len(queue_project_reanalysis(project, request.user)) for project in queryset)
        self.message_user(request, f"Queued {count} document(s) for reanalysis.")

    def get_changelist(self, request, **kwargs):
        return ProjectPageSizeChangeList

    def changelist_view(self, request, extra_context=None):
        selected = request.GET.get("page_size", "20")
        if selected not in {"10", "20", "50", "all"}:
            selected = "20"
        context = dict(extra_context or {})
        context["project_page_size"] = selected
        show_inactive_organizations = request.GET.get("show_inactive_organizations") == "true"
        scope_query = request.GET.copy()
        if show_inactive_organizations:
            scope_query.pop("show_inactive_organizations", None)
        else:
            scope_query["show_inactive_organizations"] = "true"
        context["show_inactive_organizations"] = show_inactive_organizations
        context["organization_scope_toggle_url"] = "?" + scope_query.urlencode()
        context["run_reanalysis_cart_url"] = reverse(
            "admin:platform_api_project_run_reanalysis_cart", current_app=self.admin_site.name,
        )
        return super().changelist_view(request, context)

    def get_urls(self):
        opts = self.model._meta
        custom = self.layout_bulk_urls() + [
            path("jobs/", self.admin_site.admin_view(self.jobs_view), name="platform_api_project_jobs"),
            path("jobs/status/", self.admin_site.admin_view(self.jobs_status_view), name="platform_api_project_jobs_status"),
            path("<uuid:object_id>/external-sources/", self.admin_site.admin_view(self.external_sources_view),
                 name="platform_api_project_external_sources"),
            path("layout-cart/", self.admin_site.admin_view(self.layout_cart_view),
                 name="platform_api_project_layout_cart"),
            path("<uuid:object_id>/crop-cart/", self.admin_site.admin_view(self.draft_crop_action),
                 name="platform_api_project_crop_cart"),
            path("<uuid:object_id>/crop-cart/<uuid:draft_id>/preview/", self.admin_site.admin_view(self.draft_crop_preview),
                 name="platform_api_project_crop_draft_preview"),
            path(
                "<uuid:object_id>/source-document/<uuid:document_id>/",
                self.admin_site.admin_view(self.source_document_view),
                name="platform_api_project_source_document",
            ),
            path(
                "<uuid:object_id>/add-source-pages/",
                self.admin_site.admin_view(self.add_source_pages_view),
                name="platform_api_project_add_source_pages",
            ),
            path(
                "<uuid:object_id>/source-preview/<uuid:document_id>/<int:page_number>/",
                self.admin_site.admin_view(self.source_preview_view),
                name="platform_api_project_source_preview",
            ),
            path(
                "reanalysis-cart/run/",
                self.admin_site.admin_view(self.run_reanalysis_cart_view),
                name=f"{opts.app_label}_{opts.model_name}_run_reanalysis_cart",
            ),
            path(
                "<path:object_id>/media-dashboard/",
                self.admin_site.admin_view(self.media_dashboard_view),
                name=f"{opts.app_label}_{opts.model_name}_media_dashboard",
            ),
            path(
                "<path:object_id>/merge/",
                self.admin_site.admin_view(self.merge_project_view),
                name=f"{opts.app_label}_{opts.model_name}_merge",
            ),
        ]
        return custom + super().get_urls()

    def layout_cart_view(self, request):
        from django.core.exceptions import PermissionDenied
        from django.db.models import Count
        if not request.user.has_perm("platform_api.change_floorplan") or not self.has_change_permission(request):
            raise PermissionDenied
        groups = LayoutCropDraft.objects.filter(owner=request.user).values(
            "project_id", "project__submitted_name"
        ).annotate(count=Count("pk")).order_by("project__submitted_name")
        entries = [{"name": row["project__submitted_name"], "count": row["count"],
                    "url": reverse("admin:platform_api_project_media_dashboard", args=[row["project_id"]])
                        + "?section=layouts#draft-crop-cart"} for row in groups]
        response = TemplateResponse(request, "admin/platform_api/layout_cart.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta,
            "title": "Your draft crop cart", "cart_projects": entries,
            "cart_count": sum(row["count"] for row in entries),
            "projects_url": reverse("admin:platform_api_project_changelist"),
        })
        response["Cache-Control"] = "private, no-store"
        return response

    def run_reanalysis_cart_view(self, request):
        if not self.has_change_permission(request):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        if request.method != "POST":
            return HttpResponseRedirect(reverse("admin:platform_api_project_changelist"))
        from .queue_monitor import start_user_queue
        try:
            worker = start_user_queue(request.user)
        except (OSError, ValueError) as exc:
            messages.error(request, str(exc) if isinstance(exc, ValueError) else "Could not start the local worker.")
        except WorkAlreadyClaimed:
            messages.warning(request, "The queue launcher is busy. Your jobs remain queued; try again shortly.")
        else:
            self._queue_launch_message(request, worker, "Your evaluations across all projects")
        return HttpResponseRedirect(reverse("admin:platform_api_project_jobs"))

    @staticmethod
    def _queue_launch_message(request, worker, scope):
        if worker is None or worker.state == "stopped":
            messages.info(request, "No pending evaluations to run. Completed and dead-letter jobs are unchanged.")
        elif worker.state == "queued":
            messages.warning(request, f"{scope} had to be queued because all worker slots are busy. They will start when a slot becomes available.")
        elif worker.state == "failed":
            messages.error(request, "Could not start a worker. Evaluation jobs remain queued; check worker logs and retry.")
        else:
            messages.success(request, f"{scope} are starting or already running in the background.")

    @staticmethod
    def _evaluation_redirect(request, target):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            from django.http import JsonResponse
            response = JsonResponse({"redirectUrl": target})
            response["Cache-Control"] = "private, no-store"
            return response
        return HttpResponseRedirect(target)

    def jobs_view(self, request):
        from django.core.exceptions import ValidationError
        from .queue_monitor import delete_dead_letter, queue_snapshot, requeue_dead_letter
        if request.method == "POST":
            action = request.POST.get("action")
            if action not in {"delete_dead_letter", "requeue_dead_letter"}:
                from django.http import HttpResponseBadRequest
                return HttpResponseBadRequest("Unknown queue action.")
            try:
                if action == "requeue_dead_letter":
                    requeue_dead_letter(request.user, request.POST.get("message_id"), request.POST.get("version"))
                else:
                    delete_dead_letter(request.user, request.POST.get("message_id"), request.POST.get("version"),
                                      confirmed=request.POST.get("confirm_delete") == "yes")
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
            except WorkAlreadyClaimed:
                messages.warning(request, "A worker has claimed this job. Reload before changing it.")
            else:
                messages.success(request, "Dead-letter job requeued." if action == "requeue_dead_letter" else
                                 "Dead-letter job deleted. Project data and audit history were retained.")
            return HttpResponseRedirect(reverse("admin:platform_api_project_jobs"))
        response = TemplateResponse(request, "admin/platform_api/queue_monitor.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta,
            "title": "Evaluation jobs", "snapshot": queue_snapshot(request.user),
            "station_correction_count": StationCandidate.objects.filter(needs_review=True).count()
                if request.user.has_perm("platform_api.view_stationcandidate") else None,
            "can_run": request.user.has_perms(["platform_api.change_project", "platform_api.change_floorplan"]),
        })
        response["Cache-Control"] = "private, no-store"
        return response

    def jobs_status_view(self, request):
        from django.http import JsonResponse
        from .queue_monitor import queue_snapshot
        response = JsonResponse(queue_snapshot(request.user))
        response["Cache-Control"] = "private, no-store"
        return response

    def change_view(self, request, object_id, form_url="", extra_context=None):
        context = dict(extra_context or {})
        obj = self.get_object(request, object_id)
        if obj and self.has_view_or_change_permission(request, obj):
            from .external_sources_admin import project_transit_maps
            from .layout_pricing import pricing_layouts

            context["project_transit_maps"] = project_transit_maps(obj)
            context["project_layouts"] = pricing_layouts(can_review=True).filter(
                pricing_project_id=obj.pk,
            ).order_by("configuration_type", "plan_name", "pk")
            context["project_layout_price_range"] = project_price_label(obj)
            context["can_view_layout_records"] = request.user.has_perm(
                "platform_api.view_floorplan",
            ) or request.user.has_perm("platform_api.change_floorplan")
            from django.conf import settings

            reference = ReviewTask.objects.filter(LOCATION_REFERENCE_QUERY, project=obj,
                task_type="external_sources").order_by("-created_at", "-pk").first()
            context["transit_reference_id"] = reference.pk if reference else None
            context["transit_maps_configured"] = bool(getattr(settings, "GOOGLE_MAPS_API_KEY", ""))
            if obj.status == "retired":
                messages.warning(request, "Retired project: these records are retained for reference, not active use.")
            context["project_media_dashboard_url"] = reverse(
                "admin:platform_api_project_media_dashboard", args=[obj.pk],
                current_app=self.admin_site.name,
            )
            filters = self.get_preserved_filters(request)
            if filters:
                context["project_media_dashboard_url"] += "?" + filters
            context["project_merge_url"] = reverse(
                "admin:platform_api_project_merge", args=[obj.pk], current_app=self.admin_site.name,
            )
        return super().change_view(request, object_id, form_url, context)

    def merge_project_view(self, request, object_id):
        source = self.get_object(request, object_id)
        if source is None:
            from django.http import Http404
            raise Http404
        if not self.has_change_permission(request, source):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        form = ProjectMergeForm(source, request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                merge_projects(
                    source.id, form.cleaned_data["target_project"].id, request.user,
                    form.cleaned_data["reason_code"], form.cleaned_data["notes"],
                )
            except ValueError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, "Projects joined. Source evidence remains auditable under the canonical project dashboard.")
                return HttpResponseRedirect(reverse(
                    "admin:platform_api_project_change", args=[form.cleaned_data["target_project"].id],
                    current_app=self.admin_site.name,
                ))
        return TemplateResponse(request, "admin/platform_api/project_merge.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta, "original": source,
            "title": f"Join {source.submitted_name} into a canonical project", "form": form,
        })

    @admin.display(description="Project media")
    def media_dashboard_link(self, obj):
        if not obj or not obj.pk:
            return "Save the project first."
        url = reverse(
            "admin:platform_api_project_media_dashboard", args=[obj.pk],
            current_app=self.admin_site.name,
        )
        filters = getattr(obj, "admin_preserved_filters", "")
        if filters:
            url += "?" + filters
        return format_html('<a class="button" href="{}">Open media dashboard</a>', url)

    @staticmethod
    def _layout_card(plan, dashboard_project=None):
        from .floor_plan_rules import plan_source_kind
        from .domain.room_dimensions import calculate_room_areas
        source_kind = plan_source_kind(plan)
        dimensions = []
        for item in calculate_room_areas(plan.dimensions):
            length, width = item.get("lengthFt"), item.get("widthFt")
            dimensions.append({
                "room": item.get("room") or item.get("name") or "Room",
                "length": length, "width": width, "area": item["areaSqFt"],
                "raw": json.dumps(item, indent=2, default=str),
            })
        manual_crop_url = reverse(
            "admin:platform_api_floorplan_manual_crops", args=[plan.pk],
        )
        return {
            "plan": plan,
            "priceForm": LayoutPriceForm(initial={
                "floor_plan_id": plan.pk, "expected_updated_at": plan.updated_at.isoformat(), "price": plan.price,
            }, auto_id=f"id_layout_price_{plan.pk}_%s") if plan.plan_scope == "flat" else None,
            "inactive": not plan.active or getattr(plan, "inactive_source", False),
            "editCropUrl": reverse("admin:platform_api_floorplan_crop_update", args=[plan.pk]) if plan.plan_scope in {"flat", "floor"} and plan.asset_id else None,
            "editExtractionUrl": reverse("admin:platform_api_floorplan_edit_extraction", args=[plan.pk]) if plan.plan_scope in {"flat", "floor"} and plan.asset_id else None,
            "isCroppedUnit": bool(plan.asset_id and plan.asset.asset_type == "unit_crop"),
            "imageDescription": "Saved unit crop" if plan.asset_id and plan.asset.asset_type == "unit_crop" else (
                "Pre-cut layout source" if source_kind == "precut_source" else "Source page — no individual crop saved yet"),
            "sourceKind": source_kind,
            "isPreferredSource": plan.plan_scope == "flat" and source_kind == "precut_source",
            "canNormalize": source_kind == "generated_unit_crop",
            "manualCropUrl": manual_crop_url,
            "removeUrl": reverse("admin:platform_api_floorplan_visibility", args=[
                plan.pk, dashboard_project.pk if dashboard_project else plan.project_id, "remove",
            ]),
            "imageUrl": reverse("floor-plan-asset-media", kwargs={"asset_id": plan.asset_id}) if plan.asset_id else None,
            "dimensions": dimensions,
            "features": json.dumps(plan.features, indent=2, default=str),
            "rawExtraction": json.dumps(plan.raw_extraction, indent=2, default=str),
        }

    def media_dashboard_view(self, request, object_id):
        project = self.get_object(request, object_id)
        if project is None:
            from django.http import Http404
            raise Http404
        if not self.has_view_or_change_permission(request, project):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied

        section = request.GET.get("section", "layouts")
        include_candidates = request.GET.get("include_candidates") == "1"
        if section not in {"amenities", "floor-plans", "layouts", "project-images"}:
            section = "layouts"
        if request.method == "POST":
            if not self.has_change_permission(request, project):
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied
            action = request.POST.get("action", "report")
            if action == "save_project_picture":
                from django.db import transaction

                with transaction.atomic():
                    project = Project.objects.select_for_update().get(pk=project.pk)
                    if project.status == "retired" or project.updated_at.isoformat() != request.POST.get("version"):
                        messages.error(request, "The project changed or is retired. Reload before saving its picture.")
                    else:
                        picture_form = ProjectMediaPictureForm(request.POST, request.FILES, instance=project)
                        if picture_form.is_valid():
                            project = picture_form.save(commit=False)
                            project.save(update_fields=["project_pic", "updated_at"])
                            AuditEvent.objects.create(actor=request.user, action="project.picture_changed",
                                entity_type="Project", entity_id=project.pk, correlation_id=uuid4(),
                                metadata={"source": picture_form.cleaned_data.get("project_picture_source") or "upload_or_clear"})
                            self.log_change(request, project, [{"changed": {"fields": ["Project pic"]}}])
                            messages.success(request, "Project picture saved.")
                        else:
                            for errors in picture_form.errors.values():
                                for error in errors:
                                    messages.error(request, error)
                return HttpResponseRedirect(request.path + "?section=project-images")
            if action == "add_manual_amenities":
                if project.status in {"retired", "merged"}:
                    messages.error(request, "Amenities cannot be changed on a retired or merged project.")
                else:
                    add_manual_amenities(request, project, request.POST.get("manual_amenities", ""), self.message_user)
                return HttpResponseRedirect(request.path + "?section=amenities")
            from .reanalysis import evaluation_retry_warning
            warning = evaluation_retry_warning(project, request.user, request.POST)
            if warning:
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    from django.http import JsonResponse
                    response = JsonResponse({"requiresConfirmation": True, **warning}, status=409)
                else:
                    response = TemplateResponse(request, "admin/platform_api/evaluation_retry_confirmation.html", {
                        **self.admin_site.each_context(request), "opts": self.model._meta,
                        "title": "Confirm evaluation retry", "warning": warning,
                        "cancel_url": request.get_full_path(),
                        "submitted_fields": [(key, value) for key, values in request.POST.lists()
                            if key not in {"csrfmiddlewaretoken", "retry_confirmation"} for value in values],
                    }, status=409)
                response["Cache-Control"] = "private, no-store"
                return response
            if action == "set_plan_active":
                from .floor_plan_activity import set_plan_active_locked
                from django.db import transaction
                from django.http import JsonResponse
                from django.shortcuts import get_object_or_404
                from django.core.exceptions import PermissionDenied
                asynchronous = request.headers.get("X-Requested-With") == "XMLHttpRequest"
                if not request.user.has_perm("platform_api.change_floorplan"):
                    raise PermissionDenied
                with transaction.atomic():
                    plan = get_object_or_404(FloorPlan.objects.select_for_update(),
                        pk=request.POST.get("floor_plan_id"), project_id__in=project_family_ids(project))
                    if project.status == "retired" or plan.updated_at.isoformat() != request.POST.get("version"):
                        if asynchronous:
                            response = JsonResponse({"type": "about:blank", "title": "Plan cannot be changed",
                                "status": 409, "detail": "The plan changed or the project is retired. Reload before saving."},
                                status=409, content_type="application/problem+json")
                            response["Cache-Control"] = "private, no-store"
                            return response
                        messages.error(request, "The plan changed or the project is retired. Reload before saving.")
                    else:
                        set_plan_active_locked(plan, request.POST.get("active") == "true", request.user)
                        if asynchronous:
                            related = list(visible_floor_plans(FloorPlan.objects.filter(project_id__in=project_family_ids(project),
                                document_id=plan.document_id, page_number=plan.page_number)))
                            inactive_source = any(item.plan_scope == "floor" and not item.active for item in related)
                            response = JsonResponse({"projectPrice": project_price_label(project), "plans": [{"id": str(item.pk), "active": item.active,
                                "inactive": not item.active or (item.plan_scope != "floor" and inactive_source),
                                "version": item.updated_at.isoformat()} for item in related]})
                            response["Cache-Control"] = "private, no-store"
                            return response
                return HttpResponseRedirect(request.path + f"?section={section}")
            if action == "save_layout_price":
                from django.core.exceptions import ValidationError
                from .layout_pricing import save_layout_price

                price_form = LayoutPriceForm(request.POST)
                if price_form.is_valid():
                    try:
                        save_layout_price(
                            project, price_form.cleaned_data["floor_plan_id"], price_form.cleaned_data["price"],
                            request.user, expected_updated_at=price_form.cleaned_data["expected_updated_at"],
                        )
                    except (ValidationError, ValueError) as exc:
                        messages.error(request, " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
                    else:
                        messages.success(request, "Layout price saved. The project price range is calculated from available layout prices.")
                else:
                    for field, errors in price_form.errors.items():
                        messages.error(request, f"{price_form.fields[field].label or field}: {' '.join(errors)}")
                return HttpResponseRedirect(request.path + "?section=layouts")
            if action == "save_layout_name":
                from django.core.exceptions import ValidationError
                from .layout_names import save_layout_name
                try:
                    save_layout_name(project, request.POST.get("floor_plan_id"), request.POST.get("display_name", ""), request.user)
                except ValidationError as exc:
                    messages.error(request, " ".join(exc.messages))
                else:
                    messages.success(request, "Display name saved. Extraction identity and provenance are unchanged.")
                return HttpResponseRedirect(request.path + f"?section={section}")
            if action == "run_layout_evaluation":
                from django.core.exceptions import ValidationError
                from .layout_evaluation_status import queue_single_layout
                try:
                    result = queue_single_layout(project, request.POST.get("floor_plan_id"), request.user,
                                                 retry=request.POST.get("retry") == "true")
                except (ValueError, ValidationError, OSError, WorkAlreadyClaimed):
                    messages.error(request, "Could not queue this layout. Check that its source is clean and the layout is active.")
                else:
                    if result["status"] in {"queued", "running"}:
                        messages.success(request, "Extraction queued (or existing queued/running task reused). Continue selecting other layouts; process jobs from the evaluation queue.")
                    elif result["status"] == "completed":
                        messages.info(request, "This image has already been extracted. Its completed result was retained; no duplicate job was created.")
                    else:
                        messages.warning(request, f"Existing extraction is {result['status']}. Review its status before retrying.")
                return self._evaluation_redirect(request, request.path + "?section=layouts")
            if action == "queue_amenity_pages":
                from .source_page_additions import queue_saved_amenity_pages
                try:
                    jobs = queue_saved_amenity_pages(project, request.user)
                except (ValueError, OSError, WorkAlreadyClaimed):
                    messages.error(request, "Could not queue saved amenity pages. Check the profile and clean source, then retry when not busy.")
                else:
                    pending = sum(not job.published_at and not job.dead_lettered_at for job in jobs)
                    messages.success(request, f"{pending} saved amenity page(s) awaiting extraction. Completed results were preserved.")
                return self._evaluation_redirect(request, request.path + "?section=amenities")
            if action == "queue_floor_layouts":
                try:
                    plan = FloorPlan.objects.get(pk=request.POST.get("floor_plan_id"), project_id__in=project_family_ids(project))
                    job = queue_floor_plan_layouts(plan, request.user, retry=True)
                except (FloorPlan.DoesNotExist, TypeError):
                    messages.error(request, "Choose a valid full-floor source from this project.")
                except ValueError as exc:
                    detail = {
                        "STALE_VISION_SELECTION": "The existing extraction is pinned to AI settings that have changed. Restore those settings before retrying; the job was not requeued.",
                        "SOURCE_ASSET_NOT_READY": "The floor-plan image must be ready and its source document must pass the clean-source check.",
                        "FULL_FLOOR_SOURCE_REQUIRED": "Choose a full-floor source, not an individual layout crop.",
                        "Feature is disabled.": "Automatic floor-plan segmentation is disabled. Check the media.auto_segment feature flag.",
                    }.get(str(exc), "Could not queue this floor plan. Check source readiness and the configured AI profile.")
                    messages.error(request, detail)
                else:
                    messages.success(request, "Individual-layout extraction queued (or existing task retained). Run queued evaluations; the original floor plan is preserved.")
                return self._evaluation_redirect(request, request.path + "?section=floor-plans")
            if action in {"queue_project_reanalysis", "queue_failed_reanalysis"}:
                queued = queue_project_reanalysis(project, request.user, failed_only=action == "queue_failed_reanalysis")
                messages.success(request, f"Added {len(queued)} document(s) to the reanalysis queue.")
                return self._evaluation_redirect(request, request.path + f"?section={section}")
            if action == "run_reanalysis_cart":
                return self.run_reanalysis_cart_view(request)
            if action == "run_project_evaluations":
                from .queue_monitor import start_user_queue
                try:
                    worker = start_user_queue(request.user, project=project)
                except (OSError, ValueError) as exc:
                    messages.error(request, str(exc) if isinstance(exc, ValueError) else "Could not start the local worker. Jobs remain queued.")
                except WorkAlreadyClaimed:
                    messages.warning(request, "The queue launcher is busy. Project jobs remain queued; try again shortly.")
                else:
                    self._queue_launch_message(request, worker, "Evaluations for this project")
                return self._evaluation_redirect(request, request.path + f"?section={section}")
            if action == "mark_layout_page":
                try:
                    document = EvidenceDocument.objects.get(
                        project_id__in=project_family_ids(project), id=request.POST.get("document_id")
                    )
                    page_number = int(request.POST.get("page_number", "0"))
                    if page_number < 1:
                        raise ValueError("Invalid page")
                except (EvidenceDocument.DoesNotExist, TypeError, ValueError):
                    messages.error(request, "Choose a valid source document and page to crop.")
                else:
                    return HttpResponseRedirect(reverse("admin:platform_api_project_add_source_pages", args=[project.pk])
                        + "?" + urlencode({"category": "layouts", "document": document.pk, "page_number": page_number}))
                return HttpResponseRedirect(request.path + "?section=layouts")
            plan_id = request.POST.get("floor_plan_id", "")
            if action == "normalize":
                try:
                    rotation = int(request.POST.get("rotation_degrees", "0"))
                    normalize_layout_image(plan_id, rotation, actor=request.user)
                except (FloorPlan.DoesNotExist, TypeError, ValueError) as exc:
                    messages.error(request, f"Layout normalization failed: {exc}")
                else:
                    messages.success(request, "Layout image rotated, trimmed, and normalized consistently.")
                return HttpResponseRedirect(reverse(
                    "admin:platform_api_project_media_dashboard", args=[project.pk],
                    current_app=self.admin_site.name,
                ) + "?section=layouts")
            reason_code = request.POST.get("reason_code", "").strip()
            notes = request.POST.get("notes", "").strip()[:1000]
            allowed_reasons = {
                "BAD_CROP", "MISSING_ROOMS", "WRONG_DIMENSIONS", "WRONG_FLAT_BOUNDARY", "DUPLICATE", "OTHER",
            }
            plan = visible_floor_plans(FloorPlan.objects.filter(
                project_id__in=project_family_ids(project), id=plan_id, plan_scope="flat"
            )).first()
            if plan is None or reason_code not in allowed_reasons:
                messages.error(request, "Select a valid layout and report reason.")
            else:
                existing = ReviewTask.objects.filter(
                    project=project, task_type="layout_quality", state="open",
                    resolution__floorPlanId=str(plan.id),
                ).first()
                if existing:
                    messages.warning(request, "This layout already has an open quality report.")
                else:
                    task = ReviewTask.objects.create(
                        project=project, task_type="layout_quality", priority=1, state="open",
                        resolution={
                            "floorPlanId": str(plan.id), "sourceAssetId": str(plan.asset_id) if plan.asset_id else None,
                            "reasonCode": reason_code, "notes": notes, "reportedBy": request.user.id,
                            "reportedFrom": "project_media_dashboard",
                        },
                    )
                    AuditEvent.objects.create(
                        actor=request.user, action="floor_plan.layout_quality_reported",
                        entity_type="FloorPlan", entity_id=plan.id, correlation_id=uuid4(),
                        reason_code=reason_code,
                        metadata={"projectId": str(project.id), "reviewTaskId": str(task.id)},
                    )
                    messages.success(request, "Layout reported. A high-priority review task was created.")
            return HttpResponseRedirect(reverse(
                "admin:platform_api_project_media_dashboard", args=[project.pk],
                current_app=self.admin_site.name,
            ) + "?section=layouts")

        family_ids = project_family_ids(project)
        from .models import OutboxMessage
        family_plan_ids = [str(value) for value in FloorPlan.objects.filter(
            project_id__in=family_ids
        ).exclude(review_state=REMOVED_REVIEW_STATE).values_list("id", flat=True)]
        update_cart = OutboxMessage.objects.filter(
            event_type__in=["floor_plan.crop_evaluation.requested", PAGE_EVENT, UNITS_EVENT],
            published_at__isnull=True,
        ).filter(models.Q(payload__floorPlanId__in=family_plan_ids)
                 | models.Q(payload__projectId__in=[str(pk) for pk in family_ids])).order_by("occurred_at")
        update_cart_count = update_cart.filter(dead_lettered_at__isnull=True).count()
        failed_cart_count = update_cart.filter(dead_lettered_at__isnull=False).count()
        assets = visible_plan_assets(FloorPlanAsset.objects.filter(
            project_id__in=family_ids, status="ready"
        )).order_by("page_number", "created_at")
        amenities = assets.filter(source_bounds__category="amenities").select_related("workflow_run")
        amenity_claims = project.project_amenities.select_related("amenity").order_by(
            "amenity__display_name"
        )
        plans = visible_floor_plans(FloorPlan.objects.filter(project_id__in=family_ids)).select_related("asset", "document", "workflow_run").order_by(
            "page_number", "plan_name", "created_at"
        )
        layout_sort = request.GET.get("sort", "priority")
        def natural_unit_key(plan):
            return [int(value) if value.isdigit() else value.casefold() for value in re.split(r"(\d+)", plan.display_title)]
        floor_plans = list(plans.filter(plan_scope="floor"))
        floor_plans.sort(key=natural_unit_key, reverse=layout_sort == "name-desc")
        floor_plans.sort(key=lambda plan: not plan.active)
        inactive_pages = {(plan.document_id, plan.page_number) for plan in floor_plans if not plan.active}
        all_layouts = list(plans.filter(plan_scope="flat"))
        added_layouts = [plan for plan in all_layouts if plan.workflow_run.workflow_type == "manual_source_page"]
        all_layouts = [plan for plan in all_layouts if plan.workflow_run.workflow_type != "manual_source_page"]
        # Human additions never advance the automatic document's latest-workflow pointer,
        # and a later manual addition never hides an earlier manual addition.
        manual_layouts = [plan for plan in all_layouts if plan.raw_extraction.get("segmentationMethod") == "manual"]
        all_layouts = [plan for plan in all_layouts if plan.raw_extraction.get("segmentationMethod") != "manual"]
        from .floor_plan_rules import plan_display_priority, plan_source_kind
        latest_document_workflows = {}
        for plan in all_layouts:
            if plan.crop_remaining or (plan.asset_id and plan.asset.asset_type == "unit_crop"):
                continue  # Adding split crops must not hide existing document-page layouts.
            current = latest_document_workflows.get(plan.document_id)
            if current is None or plan.workflow_run.created_at > current.created_at:
                latest_document_workflows[plan.document_id] = plan.workflow_run
        current_source_layouts = [
            plan for plan in all_layouts
            if plan_source_kind(plan) == "precut_source"
            and plan.workflow_run == latest_document_workflows.get(plan.document_id)
        ]
        current_page_layouts = [
            plan for plan in all_layouts
            if not plan.crop_remaining and plan.asset_id and plan.asset.asset_type != "unit_crop"
            and plan_source_kind(plan) == "document_page"
            and plan.workflow_run == latest_document_workflows.get(plan.document_id)
        ]
        generated_layouts = [plan for plan in all_layouts if plan.asset_id and (plan.asset.asset_type == "unit_crop" or plan.crop_remaining)]
        source_assets = {
            str(asset.id): asset for asset in project.floor_plan_assets.filter(
                id__in={plan.asset.source_bounds.get("sourceAssetId") for plan in generated_layouts}
            )
        }
        latest_workflows = {}
        for plan in generated_layouts:
            source_id = str(plan.raw_extraction.get("sourceAssetId", plan.asset.source_bounds.get("sourceAssetId", "")))
            source = source_assets.get(source_id)
            canonical_id = str(source.source_bounds.get("sourceAssetId", source_id)) if source else source_id
            method = plan.raw_extraction.get("segmentationMethod", plan.asset.source_bounds.get("segmentationMethod", "unknown"))
            key = (canonical_id, method)
            current = latest_workflows.get(key)
            if current is None or plan.workflow_run.created_at > current.created_at:
                latest_workflows[key] = plan.workflow_run
        active_workflow_ids = {workflow.id for workflow in latest_workflows.values()}
        current_generated = [plan for plan in generated_layouts if plan.workflow_run_id in active_workflow_ids]
        def layout_inactive(plan):
            return not plan.active or (plan.document_id, plan.page_number) in inactive_pages
        layout_order = natural_unit_key if layout_sort == "unit" else plan_display_priority
        layouts = sorted(
            list({plan.pk: plan for plan in current_source_layouts + current_generated + current_page_layouts + added_layouts + manual_layouts}.values()),
            key=lambda plan: (layout_inactive(plan), layout_order(plan)),
        )
        page_candidates = [
            plan for plan in all_layouts
            if plan not in current_source_layouts and plan not in current_page_layouts and plan not in generated_layouts
        ]
        if include_candidates:
            layouts = sorted(
                [*layouts, *page_candidates],
                key=lambda plan: (layout_inactive(plan), layout_order(plan)),
            )
        boundary_only = request.GET.get("issues") == "boundaries" or request.GET.get("boundary_only") == "1"
        if boundary_only:
            # Match the project-list count, including flagged historical visible records.
            layouts = sorted(
                project_boundary_layouts(project).select_related("asset", "document", "workflow_run"),
                key=lambda plan: (layout_inactive(plan), plan_display_priority(plan)),
            )
        for plan in layouts:
            plan.inactive_source = (plan.document_id, plan.page_number) in inactive_pages
        from .layout_evaluation_status import layout_evaluation_states
        evaluation_states = layout_evaluation_states([*floor_plans, *layouts])
        from .project_pictures import first_picture_document, picture_candidates

        project_image_count = picture_candidates(project).count()
        carousel_assets = []
        carousel_labels = {}
        seen_hashes = set()
        if section == "layouts":
            preview_plans = [plan for plan in layouts if plan.asset_id]
            preview_assets = [plan.asset for plan in preview_plans]
            carousel_labels = {plan.asset_id: plan.display_title for plan in preview_plans}
        elif section == "floor-plans":
            preview_assets = [plan.asset for plan in floor_plans if plan.asset_id]
            carousel_labels = {plan.asset_id: plan.display_title for plan in floor_plans}
        elif section == "project-images":
            preview_assets = []
        else:
            preview_assets = amenities
        for asset in preview_assets:
            if asset.sha256 in seen_hashes:
                continue
            seen_hashes.add(asset.sha256)
            carousel_assets.append(asset)
        navigation = self.record_navigation(request, project, "admin:platform_api_project_media_dashboard")
        if navigation:
            for direction in ("previous", "next"):
                if navigation[direction]:
                    separator = "&" if "?" in navigation[direction] else "?"
                    navigation[direction] += separator + urlencode({"section": section})
        filters = self.get_preserved_filters(request)
        draft_crops = list(LayoutCropDraft.objects.filter(
            owner=request.user, project=project,
        ).defer("image").select_related("source_plan__document", "document"))
        draft_new_count = sum(not draft.bounds.get("draftAction") for draft in draft_crops)
        draft_saved_count = len(draft_crops) - draft_new_count
        context = {
            **self.admin_site.each_context(request), "opts": self.model._meta, "original": project,
            **project_layout_health_context(project, admin_site_name=self.admin_site.name),
            "boundary_only": boundary_only,
            "bulk_remove_url": reverse("admin:platform_api_project_layout_bulk_remove", args=[project.pk]),
            "project_location": project.locality.strip() or "Location not recorded",
            "project_price": project_price_label(project),
            "can_edit_prices": self.has_change_permission(request, project)
                and request.user.has_perm("platform_api.change_floorplan") and project.status != "retired",
            "title": f"Project media — {project.submitted_name}", "section": section,
            "include_candidates": include_candidates,
            "layout_sort": layout_sort,
            "update_cart_count": update_cart_count,
            "failed_cart_count": failed_cart_count,
            "draft_crop_count": LayoutCropDraft.objects.filter(owner=request.user).count(),
            "draft_crops": draft_crops,
            "draft_new_count": draft_new_count,
            "draft_saved_count": draft_saved_count,
            "draft_cart_action_url": reverse("admin:platform_api_project_crop_cart", args=[project.pk]),
            "replacement_layouts": [plan for plan in layouts if plan.project_id == project.pk],
            "draft_evaluation_form": ManualCropForm(),
            "update_cart_items": [{"kind": item.event_type, "assetId": item.payload.get("assetId"),
                                   "attempts": item.attempts, "error": item.last_error_code,
                                   "stopped": bool(item.dead_lettered_at), "retryAt": item.available_after}
                                  for item in update_cart[:100]],
            "add_source_pages_url": reverse("admin:platform_api_project_add_source_pages", args=[project.pk]),
            "project_change_url": reverse(
                "admin:platform_api_project_change", args=[project.pk], current_app=self.admin_site.name,
            ) + ("?" + filters if filters else ""),
            "project_navigation": navigation,
            "counts": {"amenities": amenities.count(), "floorPlans": len(floor_plans), "layouts": len(layouts),
                       "projectImages": project_image_count},
            "project_picture_form": ProjectMediaPictureForm(instance=project) if section == "project-images" else None,
            "has_project_picture": section == "project-images" and (bool(project.project_pic) or first_picture_document(project) is not None),
            "hidden_layout_count": max(0, len(all_layouts) + len(added_layouts) + len(manual_layouts) - len(layouts)) if not boundary_only else 0,
            "carousel": [{
                "imageUrl": reverse("floor-plan-asset-media", kwargs={"asset_id": asset.id}),
                "label": carousel_labels.get(asset.pk, f"Page {asset.page_number or 'unknown'} — {asset.source_bounds.get('category', 'image')}"),
            } for asset in carousel_assets],
            "amenity_claims": amenity_claims,
            "amenities": [{
                "asset": asset,
                "imageUrl": reverse("floor-plan-asset-media", kwargs={"asset_id": asset.id}),
                "extractionStatus": asset.workflow_run.status,
                "metadata": json.dumps({"source": asset.source_bounds, "extraction": asset.workflow_run.result}, indent=2, default=str),
            } for asset in amenities],
            "floor_plans": [self._layout_card(plan, project) | {"evaluation": evaluation_states[str(plan.pk)]} for plan in floor_plans],
            "layouts": [self._layout_card(plan, project) | {"evaluation": evaluation_states[str(plan.pk)]} for plan in layouts],
            "documents": [{
                "document": document,
                "sourceUrl": reverse("media-page", kwargs={"document_id": document.id, "page_number": 1}),
                "decisions": list(document.layout_page_decisions.all()),
            } for document in EvidenceDocument.objects.filter(project_id__in=family_ids).prefetch_related(
                "layout_page_decisions"
            ).order_by("original_name")],
            "can_report": self.has_change_permission(request, project),
            "can_remove_plans": request.user.has_perm("platform_api.change_floorplan") and (
                request.user.has_perm("platform_api.view_project") or request.user.has_perm("platform_api.change_project")
            ),
        }
        return TemplateResponse(request, "admin/platform_api/project_media_dashboard.html", context)

    @admin.display(description="Structured extraction outputs")
    def extraction_outputs(self, obj):
        if not obj:
            return "Save a project and process a document first."
        url = reverse("project-extraction-output", kwargs={"project_id": obj.id})
        return format_html('<a href="{}" target="_blank" rel="noopener">View latest project outputs (JSON)</a>', url)


@admin.register(IngestionRun)
class IngestionRunAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "document", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("project__submitted_name", "document__original_name")
    readonly_fields = ("output_files",)

    @admin.display(description="Download output files")
    def output_files(self, obj):
        if not obj or not obj.summary.get("outputs"):
            return "No outputs yet; reprocess older documents to generate the expanded report."
        return format_html_join(" | ", '<a href="{}">{}</a>', [
            (reverse("ingestion-output-download", kwargs={"run_id": obj.id, "output_name": name}), name)
            for name in obj.summary["outputs"] if name in {"project", "layout-metrics", "layout-metrics-csv"}
        ])


@admin.register(ProjectMerge)
class ProjectMergeAdmin(admin.ModelAdmin):
    list_display = ("source_project", "target_project", "state", "reason_code", "merged_by", "applied_at")
    list_filter = ("state", "reason_code")
    search_fields = ("source_project__submitted_name", "target_project__submitted_name")
    readonly_fields = ("source_project", "target_project", "state", "reason_code", "notes", "merged_by", "applied_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("legal_name", "status", "verification_state", "confirm_name_changes")
    list_filter = ("status", "verification_state", "confirm_name_changes")
    search_fields = ("legal_name",)


for model in [
    Amenity,
    AuditEvent,
    Consent,
    FactCandidate,
    FeatureFlag,
    OrganizationMembership,
    ProjectFact,
    RecommendationSnapshot,
    RequirementProfile,
    ReviewTask,
    DocumentLayoutPage,
]:
    admin.site.register(model)

# Register reusable roles after the existing membership adapters.
from . import roles_admin  # noqa: F401,E402
