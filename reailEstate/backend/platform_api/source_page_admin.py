"""Admin views for adding evidence pages missed by automated detection."""

import io
from urllib.parse import urlencode
from uuid import UUID

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse

from .policies.crops import CART_LIMIT, authorize_crops
from .crop_from_page import stage_page_crops
from .crop_cart import stage_source_pages
from .floor_plan_segmentation_service import available_flat_separation_ai_profiles
from .ingestion_runtime import WorkAlreadyClaimed
from .models import EvidenceDocument, LayoutCropDraft
from .project_lifecycle import project_family_ids
from .source_page_additions import CATEGORIES, SOURCE_TYPES, read_source, render_source_pages, source_page_count


class SourcePageForm(forms.Form):
    document = forms.ModelChoiceField(queryset=EvidenceDocument.objects.none(), label="Source of truth document")
    category = forms.ChoiceField(choices=list(CATEGORIES.items()), widget=forms.HiddenInput)
    pages = forms.MultipleChoiceField(choices=[], widget=forms.CheckboxSelectMultiple, label="Pages to add")
    vision_profile_id = forms.ChoiceField(required=False, label="Evaluation profile")

    def __init__(self, documents, page_count, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["document"].queryset = documents
        self.fields["document"].label_from_instance = lambda doc: doc.original_name
        self.fields["pages"].choices = [(str(p), f"Page {p}") for p in range(1, page_count + 1)]
        try:
            profiles = available_flat_separation_ai_profiles()["profiles"]
        except ValueError:
            profiles = []
        self.fields["vision_profile_id"].choices = [("", "Configured default")] + [
            (p["id"], p["label"]) for p in profiles
        ]

    def clean_pages(self):
        pages = self.cleaned_data["pages"]
        if len(pages) > CART_LIMIT:
            raise forms.ValidationError("Save up to 20 page drafts at a time.")
        return [int(p) for p in pages]


class SourcePageCropForm(forms.Form):
    document = forms.UUIDField(widget=forms.HiddenInput)
    page_number = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    source_sha256 = forms.RegexField(regex=r"^[0-9a-f]{64}$", widget=forms.HiddenInput)
    regions_json = forms.JSONField(widget=forms.HiddenInput, initial=list)
    rotation_degrees = forms.TypedChoiceField(
        choices=[(value, str(value)) for value in (-180, -90, 0, 90, 180)],
        coerce=int, widget=forms.HiddenInput, initial=0,
    )


class SourcePageAdminMixin:
    def source_document_view(self, request, object_id, document_id):
        project = self._source_project(request, object_id)
        try:
            document = EvidenceDocument.objects.get(id=document_id, project_id__in=project_family_ids(project))
            data = read_source(document)
        except (EvidenceDocument.DoesNotExist, ValueError, OSError):
            raise Http404("Source document unavailable") from None
        response = FileResponse(io.BytesIO(data), content_type=document.declared_media_type,
                                filename=document.original_name, as_attachment=False)
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    def _source_project(self, request, object_id):
        project = self.get_object(request, object_id)
        if project is None:
            raise Http404
        if not self.has_change_permission(request, project):
            raise PermissionDenied
        return project

    def add_source_pages_view(self, request, object_id):
        project = self._source_project(request, object_id)
        documents = EvidenceDocument.objects.filter(
            project_id__in=project_family_ids(project), declared_media_type__in=SOURCE_TYPES,
        ).order_by("original_name")
        params = request.POST if request.method == "POST" else request.GET
        category = params.get("category", "layouts")
        if category not in CATEGORIES:
            raise Http404
        if category == "layouts":
            return self._add_layout_crops_view(request, project, documents)
        document = None
        error = None
        try:
            if params.get("document"):
                document = documents.get(id=UUID(params["document"]))
            count = source_page_count(document) if document else 0
        except (EvidenceDocument.DoesNotExist, ValueError, OSError) as exc:
            count = 0
            error = "The source is missing, unscanned, invalid or inaccessible. Select another source or repair its ingestion."
        form = SourcePageForm(
            documents, count, request.POST if request.method == "POST" else None,
            initial={"document": document, "category": category},
        )
        cancel_url = reverse("admin:platform_api_project_media_dashboard", args=[project.pk]) + f"?section={category}"
        if request.method == "POST" and form.is_valid() and not error:
            try:
                additions = stage_source_pages(
                    project, form.cleaned_data["document"].pk, form.cleaned_data["pages"], category,
                    request.user, form.cleaned_data["vision_profile_id"] or None,
                )
            except (ValueError, OSError, WorkAlreadyClaimed) as exc:
                form.add_error(None, str(exc) if isinstance(exc, ValueError) else "Source/cart is busy or unavailable. No drafts were saved.")
            else:
                messages.success(request, f"{len(additions)} page draft(s) saved to your cart. No project pages were added or queued.")
                return HttpResponseRedirect(reverse("admin:platform_api_project_media_dashboard", args=[project.pk]) + "?section=layouts#draft-crop-cart")
        preview_url = None
        if document and count:
            preview_url = reverse("admin:platform_api_project_source_preview", args=[project.pk, document.pk, 1])
        return TemplateResponse(request, "admin/platform_api/add_source_pages.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta, "original": project,
            "title": f"Add missing {CATEGORIES[category].lower()} pages", "form": form,
            "source_documents": documents, "selected_document": document, "category": category,
            "page_count": count, "source_error": error, "cancel_url": cancel_url,
            "preview_url": preview_url, "cart_count": LayoutCropDraft.objects.filter(owner=request.user).count(),
        })

    def _add_layout_crops_view(self, request, project, documents):
        try:
            authorize_crops(request.user, project)
        except ValueError:
            raise Http404("Use an active project for layout crops.") from None
        params = request.POST if request.method == "POST" else request.GET
        document, count, page, error = None, 0, 1, None
        try:
            if params.get("document"):
                document = documents.get(pk=UUID(params["document"]))
                count = source_page_count(document)
                page = int(params.get("page_number", "1"))
                if page < 1 or page > count:
                    raise ValueError("Invalid page")
                if request.method == "POST" and params.get("source_sha256") != document.sha256:
                    raise ValueError("Stale source")
        except (EvidenceDocument.DoesNotExist, ValueError, OSError):
            error = "Source changed, is unscanned, or is unavailable. Reload a source and page before drawing crops."
        form = SourcePageCropForm(
            request.POST if request.method == "POST" else None,
            initial={"document": document.pk if document else None, "page_number": page,
                     "source_sha256": document.sha256 if document else "", "regions_json": [],
                     "rotation_degrees": 0},
        )
        cancel_url = reverse("admin:platform_api_project_media_dashboard", args=[project.pk]) + "?section=layouts#draft-crop-cart"
        if request.method == "POST" and form.is_valid() and not error:
            try:
                result = stage_page_crops(
                    project, form.cleaned_data["document"], form.cleaned_data["page_number"],
                    form.cleaned_data["regions_json"], request.user, form.cleaned_data["source_sha256"],
                    form.cleaned_data["rotation_degrees"],
                )
            except (ValueError, OSError, WorkAlreadyClaimed) as exc:
                form.add_error(None, str(exc) if isinstance(exc, ValueError) else "Source/cart is busy or unavailable; nothing was saved. Retry shortly.")
            else:
                messages.success(request, f"Saved {len(result['draftIds'])} draft crop(s). No layouts were added or evaluated. Select more pages or attach drafts from your cart.")
                return HttpResponseRedirect(request.path + "?" + urlencode({
                    "category": "layouts", "document": str(document.pk), "page_number": page,
                }))
        preview_url = None
        if document and count and not error:
            preview_url = reverse("admin:platform_api_project_source_preview", args=[project.pk, document.pk, page])
            preview_url += "?" + urlencode({"source_sha256": document.sha256, "category": "layouts"})
        cart_count = LayoutCropDraft.objects.filter(owner=request.user).count()
        return TemplateResponse(request, "admin/platform_api/crop_from_page.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta, "original": project,
            "title": "Add missing layout — crop source page", "form": form,
            "source_documents": documents, "selected_document": document, "page_number": page,
            "page_numbers": range(1, count + 1), "page_count": count, "source_error": error,
            "preview_url": preview_url, "cancel_url": cancel_url, "cart_count": cart_count,
            "cart_remaining": max(0, CART_LIMIT - cart_count),
        })

    def source_preview_view(self, request, object_id, document_id, page_number):
        project = self._source_project(request, object_id)
        crop_preview = request.GET.get("category") == "layouts" or "source_sha256" in request.GET
        try:
            if crop_preview:
                authorize_crops(request.user, project)
            document = EvidenceDocument.objects.get(id=document_id, project_id__in=project_family_ids(project))
            if crop_preview and request.GET.get("source_sha256") != document.sha256:
                raise ValueError("Source changed; reload the cropper.")
            data = render_source_pages(document, [page_number])[0][1]
        except (EvidenceDocument.DoesNotExist, ValueError, OSError):
            raise Http404("Source page unavailable") from None
        response = HttpResponse(data, content_type="image/png")
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response