import hashlib

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.urls import reverse

from .crop_cart import _authorize, _ids, attach_drafts, commit_saved_draft, discard_drafts
from .ingestion_runtime import WorkAlreadyClaimed
from .models import LayoutCropDraft


class CropCartAdminMixin:
    def draft_crop_preview(self, request, object_id, draft_id):
        project = self.get_object(request, object_id)
        if project is None or not self.has_change_permission(request, project):
            raise PermissionDenied
        try:
            _authorize(request.user, project)
        except ValueError:
            raise Http404("Use an active project for crop drafts.") from None
        try:
            draft = LayoutCropDraft.objects.get(pk=draft_id, owner=request.user, project=project)
        except LayoutCropDraft.DoesNotExist:
            raise Http404 from None
        data = bytes(draft.image)
        if hashlib.sha256(data).hexdigest() != draft.sha256:
            raise Http404("Draft checksum mismatch")
        response = HttpResponse(data, content_type="image/png")
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response

    def draft_crop_action(self, request, object_id):
        project = self.get_object(request, object_id)
        if project is None or not self.has_change_permission(request, project):
            raise PermissionDenied
        url = reverse("admin:platform_api_project_media_dashboard", args=[project.pk]) + "?section=layouts#draft-crop-cart"
        if request.method != "POST":
            return HttpResponseRedirect(url)
        try:
            _authorize(request.user, project)
            selected = request.POST.getlist("draft_ids")
            action = request.POST.get("draft_action")
            if action == "discard":
                discard_drafts(project, request.user, selected)
                messages.success(request, "Selected draft crops discarded. Project layouts were not changed.")
            elif action == "commit_saved":
                ids = _ids(selected)
                drafts = list(LayoutCropDraft.objects.filter(owner=request.user, project=project, pk__in=ids))
                if not ids or len(drafts) != len(ids) or any(draft.bounds.get("draftAction") not in {"page", "update"} for draft in drafts):
                    raise ValueError("Select saved page additions or crop updates only.")
                if request.POST.get("confirm_commit") != "on":
                    raise ValueError("Confirm applying the selected pages/updates and queueing evaluation.")
                completed = 0
                for draft in drafts:
                    try:
                        commit_saved_draft(project, request.user, draft.pk)
                    except (ValueError, OSError, WorkAlreadyClaimed) as exc:
                        detail = str(exc) if isinstance(exc, ValueError) else "Source is busy or unavailable."
                        messages.error(request, f"{draft.label}: {detail} Draft retained.")
                    else:
                        completed += 1
                if completed:
                    messages.success(request, f"Committed {completed} saved page(s)/update(s) and queued evaluation.")
            elif action in {"add", "replace"}:
                replacements = request.POST.getlist("replacement_ids") if action == "replace" else []
                if action == "replace" and (not replacements or request.POST.get("confirm_replace") != "on"):
                    raise ValueError("Select existing layouts and confirm replacement, or use Add selected to project.")
                plans = attach_drafts(project, request.user, selected, replacements,
                                      request.POST.get("vision_profile_id") or None)
                messages.success(request, f"Added {len(plans)} selected layout(s) and queued evaluation. " +
                                 ("Only selected old layouts were replaced." if replacements else "Existing layouts remain unchanged."))
            else:
                raise ValueError("Choose Add, Commit, Replace or Discard.")
        except (ValueError, OSError, WorkAlreadyClaimed) as exc:
            messages.error(request, str(exc) if isinstance(exc, ValueError) else "Cart/source is busy or unavailable. Nothing was added; retry shortly.")
        return HttpResponseRedirect(url)