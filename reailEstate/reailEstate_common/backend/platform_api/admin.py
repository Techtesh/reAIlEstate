from copy import copy
from uuid import uuid4
from urllib.parse import urlencode

from django import forms
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.admin.options import IncorrectLookupParameters
from django.contrib.admin.views.main import ChangeList
from django.core.exceptions import SuspiciousOperation
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
    ProjectAmenity,
    ProjectMerge,
    ProjectFact,
    RecommendationSnapshot,
    RequirementProfile,
    ReviewTask,
    StationCandidate,
)
from .project_lifecycle import merge_projects, project_family_ids, retire_project
from .project_admin_actions import project_retirement_response
from .reanalysis import queue_project_reanalysis
from .source_page_admin import SourcePageAdminMixin
from .project_workspace_admin import workspace_redirect, workspace_url
from .external_sources_admin import (
    ExternalSourcesAdminMixin, GOOGLE_MATCH_LABELS, GoogleMatchFilter, MahaRERAFilter, google_match_annotations,
    LOCATION_REFERENCE_QUERY, manual_reference_details,
)
from .crop_cart_admin import CropCartAdminMixin
from .floor_plan_removal import authorize_plan_removal, set_floor_plan_removed
from .floor_plan_visibility import REMOVED_REVIEW_STATE
from .ingestion_runtime import WorkAlreadyClaimed
from .layout_crop_update_admin import LayoutCropUpdateAdminMixin
from .layout_extraction_admin import LayoutExtractionAdminMixin
from .layout_bulk_admin import LayoutBulkAdminMixin
from .project_layout_health import ProjectLayoutHealthAdminMixin, NeedsBoundaryReviewFilter


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
        context.update(show_save=False, show_save_and_continue=False, show_save_and_add_another=False)
        if request.method != "GET":
            from django.http import HttpResponseNotAllowed
            return HttpResponseNotAllowed(["GET"])
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
    readonly_fields = (
        *(field.name for field in FloorPlan._meta.fields), "saved_image", "extraction_notice", "removal_action", "pricing_editor",
    )
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

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        # Removal is reversible; the default admin delete would destroy evidence.
        return False


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
        if obj and self.has_change_permission(request, obj):
            context["layout_workspace_url"] = workspace_url(obj.project,
                section="floor-plans" if obj.plan_scope == "floor" else "layouts", plan=str(obj.pk))
        context.update(show_save=False, show_save_and_continue=False, show_save_and_add_another=False)
        if request.method != "GET":
            from django.http import HttpResponseNotAllowed
            return HttpResponseNotAllowed(["GET"])
        return super().change_view(request, object_id, form_url, context)

    def manual_crops_view(self, request, object_id):
        return workspace_redirect(self, request, object_id)


    def segment_units_view(self, request, object_id):
        return workspace_redirect(self, request, object_id)

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
    readonly_fields = (*(field.name for field in FloorPlanAsset._meta.fields), "saved_image")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
    ordering = ("-created_at",)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        context = dict(extra_context or {})
        obj = self.get_object(request, object_id)
        if obj and obj.asset_type == "unit_crop" and self.has_view_or_change_permission(request, obj):
            context["layout_dashboard_url"] = reverse(
                "admin:platform_api_project_media_dashboard", args=[obj.project_id],
                current_app=self.admin_site.name,
            ) + "?section=layouts"
        context.update(show_save=False, show_save_and_continue=False, show_save_and_add_another=False)
        if request.method != "GET":
            from django.http import HttpResponseNotAllowed
            return HttpResponseNotAllowed(["GET"])
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


@admin.register(Project)
class ProjectAdmin(ExternalSourcesAdminMixin, ProjectLayoutHealthAdminMixin, LayoutBulkAdminMixin, CropCartAdminMixin, SourcePageAdminMixin, ImageNavigationAdminMixin, admin.ModelAdmin):
    change_form_template = "admin/platform_api/project_change_form.html"
    change_list_template = "admin/platform_api/project_change_list.html"
    list_display = ("submitted_name", "created_at", "maharera_reference", "google_maps_reference", "locality", "status", "boundary_review_link", "media_dashboard_link")
    list_filter = ("locality", GoogleMatchFilter, MahaRERAFilter, NeedsBoundaryReviewFilter)
    search_fields = ("submitted_name", "locality", "maha_rera_id")
    readonly_fields = ("extraction_outputs", "media_dashboard_link", "google_location_reference",
                       *(field.name for field in Project._meta.fields))
    fieldsets = ((None, {
        "classes": ("project-details-grid", "project-metadata"),
        "fields": ("organization", "status", "maha_rera_id", "submitted_name",
                   "published_fact_set_version", "verification_status", "processing_attested", "verification_checked_at"),
    }), (None, {
        "classes": ("project-details-grid", "project-resources"),
        "fields": ("media_dashboard_link", "extraction_outputs"),
    }), (None, {
        "classes": ("project-details-grid", "project-address"),
        "fields": ("address", "locality"),
    }), (None, {
        "classes": ("project-details-grid", "project-map-reference"),
        "fields": ("google_location_reference",),
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
        from django.http import HttpResponseNotAllowed
        if not self.has_change_permission(request) or not request.user.has_perm("platform_api.change_floorplan"):
            raise PermissionDenied
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        return HttpResponseRedirect(reverse("admin-project-workspace") + "?cart=1")

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
        from django.http import JsonResponse
        from django.core.exceptions import ValidationError
        from .queue_monitor import delete_dead_letter, queue_snapshot, requeue_dead_letter
        project_id = request.POST.get("project_id") or request.GET.get("project")
        project = self.get_object(request, project_id) if project_id else None
        if project_id and project is None:
            from django.http import Http404
            raise Http404
        if request.method == "POST":
            action = request.POST.get("action")
            if action in {"run_project_evaluations", "run_queued_project_evaluations"}:
                from django.core.exceptions import PermissionDenied
                from django.http import HttpResponseBadRequest
                from .reanalysis import evaluation_retry_warning, queue_fresh_project_evaluations
                from .queue_monitor import start_user_queue
                if not request.user.has_perms(["platform_api.change_project", "platform_api.change_floorplan"]):
                    raise PermissionDenied
                if project is None or project.status in {"retired", "merged"}:
                    return HttpResponseBadRequest("Choose an active project.")
                warning = evaluation_retry_warning(project, request.user, request.POST)
                if warning:
                    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
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
                try:
                    if action == "run_project_evaluations" and request.POST.get("fresh_evaluation") == "true":
                        queue_fresh_project_evaluations(project, request.user)
                    worker = start_user_queue(request.user, project=project)
                except (OSError, ValueError):
                    messages.error(request, "Could not start project evaluations. Jobs remain queued; check clean sources and the configured AI profile.")
                except WorkAlreadyClaimed:
                    messages.warning(request, "The queue launcher is busy. Project jobs remain queued; try again shortly.")
                else:
                    self._queue_launch_message(request, worker, "Evaluations for this project")
                return self._evaluation_redirect(request, reverse("admin:platform_api_project_jobs") + "?" + urlencode({"project": str(project.pk)}))
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
                message, level = " ".join(exc.messages), messages.ERROR
            except WorkAlreadyClaimed:
                message, level = "A worker has claimed this job. Reload before changing it.", messages.WARNING
            else:
                message, level = ("Dead-letter job requeued." if action == "requeue_dead_letter" else
                                  "Dead-letter job deleted. Project data and audit history were retained."), messages.SUCCESS
            if request.headers.get("Accept") == "application/json":
                response = JsonResponse({"message": message, "snapshot": queue_snapshot(request.user)},
                                        status=200 if level == messages.SUCCESS else 409)
                response["Cache-Control"] = "private, no-store"
                return response
            messages.add_message(request, level, message)
            return HttpResponseRedirect(reverse("admin:platform_api_project_jobs"))
        response = TemplateResponse(request, "admin/platform_api/queue_monitor.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta,
            "title": "Evaluation jobs", "snapshot": queue_snapshot(request.user), "selected_project": project,
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

    def add_view(self, request, form_url="", extra_context=None):
        from django.core.exceptions import PermissionDenied
        if not self.has_add_permission(request) or not self.has_change_permission(request):
            raise PermissionDenied
        return HttpResponseRedirect(reverse("admin-project-workspace") + "?new=1")

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
        context.update(show_save=False, show_save_and_continue=False, show_save_and_add_another=False)
        if request.method != "GET":
            from django.http import HttpResponseNotAllowed
            return HttpResponseNotAllowed(["GET"])
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
        return format_html('<a class="button" href="{}">Open project workspace</a>', url)


    def media_dashboard_view(self, request, object_id):
        return workspace_redirect(self, request, object_id)

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


class ProjectAmenityReviewForm(forms.ModelForm):
    expected_updated_at = forms.CharField(widget=forms.HiddenInput)
    review_state = forms.ChoiceField(choices=[("unreviewed", "Unreviewed"), ("accepted", "Accepted"), ("rejected", "Rejected")])
    reason = forms.CharField(widget=forms.Textarea, max_length=1000)

    class Meta:
        model = ProjectAmenity
        fields = ("availability_state", "distance_meters", "travel_minutes")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["review_state"].initial = self.instance.evidence_summary.get("reviewState", "unreviewed")
        self.fields["expected_updated_at"].initial = self.instance.updated_at.isoformat()

    def clean(self):
        data = super().clean()
        project = Project.objects.select_for_update().get(pk=self.instance.project_id)
        current = ProjectAmenity.objects.select_for_update().get(pk=self.instance.pk)
        if data.get("expected_updated_at") != current.updated_at.isoformat():
            raise forms.ValidationError("This amenity changed. Reload and review the latest evidence before saving.")
        if project.status in {"retired", "merged"}:
            raise forms.ValidationError("Retired and merged projects are read-only.")
        if data.get("review_state") == "accepted" and not self.actor.is_superuser:
            summary = self.instance.evidence_summary
            if OrganizationMembership.objects.filter(user=self.actor, organization=self.instance.project.organization, status="active").exists() or any(
                entry.get("submittedBy") == self.actor.pk for entry in summary.get("manual", [])
            ):
                raise forms.ValidationError("A different administrator must review your amenity claims.")
        return data


@admin.register(ProjectAmenity)
class ProjectAmenityAdmin(admin.ModelAdmin):
    form = ProjectAmenityReviewForm
    list_display = ("project", "amenity", "availability_state", "updated_at")
    list_filter = ("project", "availability_state")
    readonly_fields = ("project", "amenity", "evidence_summary")
    fields = ("project", "amenity", "evidence_summary", "availability_state", "distance_meters", "travel_minutes", "review_state", "reason", "expected_updated_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        class ReviewForm(form):
            actor = request.user
        return ReviewForm

    def save_model(self, request, obj, form, change):
        from django.utils import timezone
        obj.evidence_summary = {**obj.evidence_summary,
            "reviewState": form.cleaned_data["review_state"], "reviewedBy": request.user.pk,
            "reviewedAt": timezone.now().isoformat(), "reviewReason": form.cleaned_data["reason"]}
        super().save_model(request, obj, form, change)
        AuditEvent.objects.create(actor=request.user, action="project.amenity_reviewed",
            entity_type="ProjectAmenity", entity_id=obj.pk, correlation_id=uuid4(),
            metadata={"projectId": str(obj.project_id), "reviewState": form.cleaned_data["review_state"],
                      "reason": form.cleaned_data["reason"]})


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
