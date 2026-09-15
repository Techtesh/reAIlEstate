"""Opt-in ProjectAdmin endpoint for explicit, reversible bulk layout removal.

Card integration POSTs repeated ``selected_ids`` (only checked/visible cards).
No query string, select-all flag, or initial POST can remove a record.
"""

from django import forms
from django.contrib import messages
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import OperationalError
from django.http import Http404, HttpResponseNotAllowed, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.dateparse import parse_datetime
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie

from .floor_plan_removal import (
    MAX_BULK_LAYOUTS, _pending_batch_jobs, authorize_plan_removal,
    normalize_plan_ids, remove_floor_plans,
)
from .floor_plan_visibility import visible_floor_plans
from .ingestion_runtime import WorkAlreadyClaimed
from .models import FloorPlan
from .project_lifecycle import project_family_ids

SELECTION_SALT = "platform_api.layout_bulk_remove.v1"
CONFIRMATION_MAX_AGE = 30 * 60


class LayoutBulkRemovalConfirmationForm(forms.Form):
    selection_token = forms.CharField(widget=forms.HiddenInput, max_length=100000)
    expected_updated_at = forms.JSONField(widget=forms.HiddenInput)
    confirm = forms.BooleanField(
        label="Remove only these selected layouts. Keep source evidence and allow restoration from each floor-plan record.",
        error_messages={"required": "Select the confirmation checkbox to remove these layouts."},
    )


def _private(response):
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


class LayoutBulkAdminMixin:
    """Prepend layout_bulk_urls() in ProjectAdmin.get_urls(); no registration."""

    layout_bulk_confirmation_template = "admin/platform_api/layout_bulk_removal_confirmation.html"

    def layout_bulk_urls(self):
        return [path(
            "<uuid:object_id>/layouts/bulk-remove/",
            self.admin_site.admin_view(self.layout_bulk_remove_view),
            name="platform_api_project_layout_bulk_remove",
        )]

    def _bulk_layout_project(self, request, object_id):
        project = self.get_object(request, str(object_id))
        if project is None:
            raise Http404
        if not self.has_view_or_change_permission(request, project):
            raise PermissionDenied
        authorize_plan_removal(request.user, project)
        return project

    def _bulk_layout_selection(self, project, selected_ids):
        ids = normalize_plan_ids(selected_ids)
        plans = list(visible_floor_plans(FloorPlan.objects.filter(
            pk__in=ids, project_id__in=project_family_ids(project), plan_scope="flat",
        )).select_related("document").order_by("pk"))
        if len(plans) != len(ids):
            # Do not disclose names/IDs of records outside the authorized family.
            raise Http404("One or more selected layouts are unavailable in this project.")
        return plans

    @method_decorator(csrf_protect)
    @method_decorator(ensure_csrf_cookie)
    def layout_bulk_remove_view(self, request, object_id):
        if request.method not in {"GET", "POST"}:
            return _private(HttpResponseNotAllowed(["GET", "POST"]))
        project = self._bulk_layout_project(request, object_id)
        cancel_url = reverse("admin:platform_api_project_media_dashboard", args=[project.pk],
                             current_app=self.admin_site.name) + "?section=layouts"
        plans, form, error, status = [], None, None, 200
        if request.method == "POST":
            if request.POST.get("stage", "select") == "select" and "selection_token" not in request.POST:
                try:
                    plans = self._bulk_layout_selection(project, request.POST.getlist("selected_ids"))
                except ValueError as exc:
                    error, status = str(exc), 400
                else:
                    versions = {str(plan.pk): plan.updated_at.isoformat() for plan in plans}
                    token = signing.dumps({"projectId": str(project.pk), "actorId": str(request.user.pk),
                                           "versions": versions}, salt=SELECTION_SALT, compress=True)
                    form = LayoutBulkRemovalConfirmationForm(initial={
                        "selection_token": token, "expected_updated_at": versions,
                    })
            elif request.POST.get("stage") == "confirm":
                form = LayoutBulkRemovalConfirmationForm(request.POST)
                if form.is_valid():
                    try:
                        payload = signing.loads(form.cleaned_data["selection_token"], salt=SELECTION_SALT,
                                                max_age=CONFIRMATION_MAX_AGE)
                        if (payload.get("projectId") != str(project.pk)
                                or payload.get("actorId") != str(request.user.pk)
                                or payload.get("versions") != form.cleaned_data["expected_updated_at"]):
                            raise ValueError("The selection changed. Select the layouts again.")
                        versions = payload["versions"]
                        if not isinstance(versions, dict):
                            raise ValueError("Invalid layout confirmation. Select the layouts again.")
                        # Validate every ID and datetime; never silently drop a bad entry.
                        ids = normalize_plan_ids(versions)
                        expected = {key: parse_datetime(value) for key, value in versions.items()}
                        if any(value is None for value in expected.values()):
                            raise ValueError("Invalid confirmation timestamps. Select the layouts again.")
                        result = remove_floor_plans(project, ids, request.user, expected_updated_at=expected)
                    except signing.BadSignature:
                        form.add_error(None, "This confirmation expired or was changed. Select the layouts again.")
                        status = 409
                    except (ValueError, TypeError):
                        form.add_error(None, "A layout or confirmation changed. Nothing was removed. Select the layouts again.")
                        status = 409
                    except FloorPlan.DoesNotExist:
                        raise Http404("One or more selected layouts are unavailable in this project.") from None
                    except (WorkAlreadyClaimed, OperationalError):
                        form.add_error(None, "Layouts or jobs are busy. Nothing was removed. Return to layouts and retry.")
                        status = 409
                    else:
                        messages.success(request, (
                            f"Selected {result.selected_count}; removed {result.removed_count}; "
                            f"unchanged {result.unchanged_count}; cancelled {result.cancelled_job_count} per-layout job(s). "
                            "Source evidence was kept. Restore layouts from their floor-plan admin records."
                        ))
                        return _private(HttpResponseRedirect(cancel_url))
                else:
                    status = 400
            else:
                error, status = "Select layouts and review the server confirmation first.", 400
        context = {
            **self.admin_site.each_context(request), "opts": self.model._meta, "original": project,
            "title": "Confirm removal of selected layouts", "form": form, "selection_error": error,
            "selected_layouts": plans, "selected_count": len(plans), "max_layouts": MAX_BULK_LAYOUTS,
            "pending_job_count": _pending_batch_jobs([plan.pk for plan in plans]).count() if plans else 0,
            "cancel_url": cancel_url,
        }
        return _private(TemplateResponse(request, self.layout_bulk_confirmation_template, context, status=status))