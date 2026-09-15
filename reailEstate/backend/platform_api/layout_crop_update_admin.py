"""Opt-in FloorPlanAdmin mixin; no registration or existing-admin changes."""

from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, HttpResponseNotAllowed, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect

from .ingestion_runtime import WorkAlreadyClaimed
from .crop_cart import stage_crop_update
from .layout_crop_update import (
    StaleCropEdit, crop_edit_preview, editable_layout, prepare_crop_edit,
    validate_rectangle,
)
from .models import FloorPlan, LayoutCropDraft


class LayoutCropUpdateForm(forms.Form):
    editor_token = forms.CharField(widget=forms.HiddenInput)
    expected_asset_id = forms.UUIDField(widget=forms.HiddenInput)
    expected_sha256 = forms.RegexField(regex=r"^[0-9a-f]{64}$", widget=forms.HiddenInput)
    source_sha256 = forms.RegexField(regex=r"^[0-9a-f]{64}$", widget=forms.HiddenInput)
    source_mode = forms.ChoiceField(choices=[("current", "Current cropped unit"), ("original", "Original source")],
                                    widget=forms.HiddenInput)
    regions = forms.JSONField(widget=forms.HiddenInput, initial=list)
    rotation_degrees = forms.TypedChoiceField(
        choices=[(value, str(value)) for value in (0, 90, 180, 270)], coerce=int,
        widget=forms.HiddenInput, initial=0,
    )
    confirm = forms.BooleanField(
        label="Save this proposed update to my draft cart.",
        error_messages={"required": "Confirm saving this update as a draft."},
    )

    def clean_regions(self):
        regions = self.cleaned_data["regions"]
        try:
            validate_rectangle(regions)
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from exc
        return regions


def _private(response):
    response["Cache-Control"] = "private, no-store"
    response["Pragma"] = "no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    response["Referrer-Policy"] = "same-origin"
    return response


class LayoutCropUpdateAdminMixin:
    """Main integration prepends layout_crop_update_urls() in get_urls()."""

    def layout_crop_update_urls(self):
        # Explicit helper avoids MRO/get_urls surprises in the existing admin.
        return [
            path("<uuid:object_id>/edit-crop/", self.admin_site.admin_view(self.layout_crop_update_view),
                 name="platform_api_floorplan_crop_update"),
            path("<uuid:object_id>/edit-crop/preview/", self.admin_site.admin_view(self.layout_crop_update_preview),
                 name="platform_api_floorplan_crop_update_preview"),
        ]

    def _crop_target(self, request, object_id):
        obj = self.get_object(request, str(object_id))
        if obj is None:
            raise Http404
        if not self.has_change_permission(request, obj):
            raise PermissionDenied
        try:
            return editable_layout(obj.pk, request.user)
        except (FloorPlan.DoesNotExist, ValueError):
            raise Http404("Layout is not available for crop editing.") from None

    def layout_crop_update_preview(self, request, object_id):
        if request.method != "GET":
            return _private(HttpResponseNotAllowed(["GET"]))
        self._crop_target(request, object_id)
        try:
            data = crop_edit_preview(object_id, request.user, request.GET.get("token", ""))
        except (ValueError, OSError, WorkAlreadyClaimed):
            return _private(HttpResponse("Preview unavailable; reload the crop editor.", status=409, content_type="text/plain"))
        # Both supported formats are authenticated here, never through public media.
        media_type = "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg"
        return _private(HttpResponse(data, content_type=media_type))

    @method_decorator(csrf_protect)
    def layout_crop_update_view(self, request, object_id):
        if request.method not in {"GET", "POST"}:
            return _private(HttpResponseNotAllowed(["GET", "POST"]))
        obj, destination = self._crop_target(request, object_id)
        editor_url = reverse("admin:platform_api_floorplan_crop_update", args=[obj.pk], current_app=self.admin_site.name)
        cancel_url = reverse("admin:platform_api_project_media_dashboard", args=[destination.pk],
                             current_app=self.admin_site.name) + "?section=layouts"
        status = 200
        source_error = None
        state = None
        preview_url = None
        if request.method == "POST":
            form = LayoutCropUpdateForm(request.POST)
            if form.is_valid():
                try:
                    stage_crop_update(obj.pk, request.user, **form.cleaned_data)
                except StaleCropEdit as exc:
                    form.add_error(None, str(exc))
                    status = 409
                except ValueError as exc:
                    form.add_error(None, str(exc))
                    status = 400
                except (OSError, WorkAlreadyClaimed):
                    form.add_error(None, "The source is busy or unavailable. No crop was saved; reload and retry.")
                    status = 409
                else:
                    messages.success(request, "Crop update saved to your cart. The layout and evaluation queue are unchanged.")
                    return _private(HttpResponseRedirect(cancel_url + "#draft-crop-cart"))
            else:
                status = 400
            # Never rebase failed POST coordinates onto a new image/version or
            # silently revert Original source to Current. Reload is deliberate.
            source_error = "Nothing was saved. Reload the chosen source and select a new rectangle."
            mode = request.POST.get("source_mode", "current")
        else:
            mode = request.GET.get("source_mode", "current")
            try:
                obj, destination, state, token = prepare_crop_edit(obj.pk, request.user, source_mode=mode)
                form = LayoutCropUpdateForm(initial={
                    "editor_token": token, "expected_asset_id": state["expected_asset_id"],
                    "expected_sha256": state["expected_sha256"], "source_sha256": state["source_sha256"],
                    "source_mode": mode, "rotation_degrees": 0, "regions": [],
                })
                preview_url = reverse("admin:platform_api_floorplan_crop_update_preview", args=[obj.pk],
                                      current_app=self.admin_site.name) + "?" + urlencode({"token": token})
            except (ValueError, OSError, WorkAlreadyClaimed):
                form = LayoutCropUpdateForm()
                source_error = "This source is busy, changed or unavailable. Reload it or explicitly choose another source mode."
                status = 409
        context = {
            **self.admin_site.each_context(request), "opts": self.model._meta, "original": obj,
            "title": f"Edit crop — {obj.display_title}", "form": form, "source_mode": mode,
            "cart_count": LayoutCropDraft.objects.filter(owner=request.user).count(),
            "source_error": source_error, "preview_url": preview_url, "cancel_url": cancel_url,
            "current_url": editor_url, "original_url": editor_url + "?source_mode=original",
            "reload_url": editor_url + ("?source_mode=original" if mode == "original" else ""),
            "crop_config": {"previewUrl": preview_url, "sourceMode": mode,
                            "width": state["width"] if state else 0, "height": state["height"] if state else 0},
        }
        return _private(TemplateResponse(request, "admin/platform_api/layout_crop_update.html", context, status=status))