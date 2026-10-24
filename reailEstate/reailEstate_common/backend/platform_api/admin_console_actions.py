"""Adapters for the custom queue, review and project lifecycle screens."""
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .api_common import AdminAPIView
from .admin_workspace_views import independent, require_permission
from .builder_views import BuilderConflict
from .ingestion_runtime import WorkAlreadyClaimed
from .models import Project, ReviewTask
from .services import audit


class ConsoleAction(AdminAPIView):
    permission_classes = [IsAdminUser]

    def handle_exception(self, exc):
        if isinstance(exc, (ValueError, DjangoValidationError)):
            exc = ValidationError(str(exc))
        elif isinstance(exc, WorkAlreadyClaimed):
            exc = BuilderConflict("This work is already claimed. Refresh before retrying.")
        return super().handle_exception(exc)


class ConsoleQueue(ConsoleAction):
    def get(self, request):
        from .queue_monitor import queue_snapshot
        project = get_object_or_404(Project, pk=request.query_params["project"]) if request.query_params.get("project") else None
        return Response({**queue_snapshot(request.user, project=project), "canRun": request.user.has_perms(["platform_api.change_project", "platform_api.change_floorplan"])})

    def post(self, request):
        from .queue_monitor import delete_dead_letter, requeue_dead_letter, start_user_queue, require_monitor_access
        from .reanalysis import queue_fresh_project_evaluations, evaluation_retry_warning
        require_monitor_access(request.user, run=True)
        action = serializers.ChoiceField(choices=["run", "fresh", "requeue", "delete"]).run_validation(request.data.get("action"))
        if request.data.get("confirm") is not True:
            raise ValidationError("Confirm the queue action.")
        if action in {"requeue", "delete"}:
            args = (request.user, request.data.get("id"), request.data.get("version"))
            requeue_dead_letter(*args) if action == "requeue" else delete_dead_letter(*args, confirmed=True)
        else:
            project = get_object_or_404(Project, pk=request.data["project"]) if request.data.get("project") else None
            if action == "fresh":
                if project is None:
                    raise ValidationError("Choose a project for fresh evaluations.")
                from django.http import QueryDict
                submitted = QueryDict(mutable=True)
                submitted.update({"action": "run_project_evaluations", "project_id": str(project.pk), "fresh_evaluation": "true"})
                if request.data.get("retryConfirmation"):
                    submitted["retry_confirmation"] = request.data["retryConfirmation"]
                warning = evaluation_retry_warning(project, request.user, submitted)
                if warning:
                    return Response({"detail": "Explicit retry confirmation is required.", **warning}, status=409)
                queue_fresh_project_evaluations(project, request.user)
            worker = start_user_queue(request.user, project=project)
            return Response({"state": worker.state if worker else "stopped"})
        return Response({"status": "updated"})


class ConsoleProjectAction(ConsoleAction):
    def post(self, request, project_id):
        require_permission(request.user, "project", "change")
        action = serializers.ChoiceField(choices=["retire", "merge", "reanalyse", "publish"]).run_validation(request.data.get("action"))
        project = get_object_or_404(Project, pk=project_id)
        if request.data.get("confirm") is not True:
            raise ValidationError("Confirm the project action.")
        if action == "publish":
            from .views import PublishProjectView
            return PublishProjectView().post(request, project_id)
        if action == "merge":
            from .views import ProjectMergeAdminView
            return ProjectMergeAdminView().post(request, project_id)
        if action == "retire":
            from .project_lifecycle import retire_project
            retire_project(project.pk, request.user)
        elif action == "reanalyse":
            from .reanalysis import queue_project_reanalysis
            queue_project_reanalysis(project, request.user)
        return Response({"status": "updated"})


class ConsoleTaskAction(ConsoleAction):
    def get(self, request, task_id):
        require_permission(request.user, "reviewtask")
        item = get_object_or_404(ReviewTask, pk=task_id)
        from django.contrib.auth import get_user_model
        return Response({"assignees": list(get_user_model().objects.filter(is_active=True, is_staff=True).values("id", "username")),
            "candidates": [{"id": str(candidate.pk), "label": candidate.field_code, "value": candidate.typed_value,
                "page": candidate.page_number, "confidence": candidate.confidence} for candidate in item.project.fact_candidates.order_by("field_code", "pk")[:500]]})

    @transaction.atomic
    def post(self, request, task_id):
        require_permission(request.user, "reviewtask", "change")
        item = get_object_or_404(ReviewTask.objects.select_for_update(), pk=task_id)
        if request.data.get("expectedVersion") != item.version:
            raise BuilderConflict("This task changed. Reload before reviewing it.")
        action = serializers.ChoiceField(choices=["resolve", "reject", "reopen", "assign"]).run_validation(request.data.get("action"))
        reason = serializers.CharField(max_length=1000).run_validation(request.data.get("reasonCode"))
        if action == "resolve":
            if item.task_type in {"external_sources", "floor_plan", "publication"}:
                raise ValidationError("Use the project evidence review workflow for this task type.")
            independent(request.user, item.project)
            from .views import ResolveReviewTaskView
            return ResolveReviewTaskView().post(request, task_id)
        if action == "assign":
            from django.contrib.auth import get_user_model
            item.assigned_to = get_object_or_404(get_user_model(), pk=request.data.get("assignedTo"), is_active=True, is_staff=True) if request.data.get("assignedTo") else None
            item.priority = serializers.IntegerField(min_value=0, max_value=100).run_validation(request.data.get("priority", item.priority))
        else:
            independent(request.user, item.project)
            if item.task_type in {"external_sources", "floor_plan", "publication"}:
                raise ValidationError("Use the project evidence review workflow for this task type.")
            item.state = "open" if action == "reopen" else "rejected"
            item.completed_at = None if action == "reopen" else timezone.now()
            item.resolution = {**item.resolution, "reasonCode": reason}
        item.version += 1; item.save()
        audit(request, f"review_task.{action}", item, reason_code=reason)
        return Response({"status": item.state, "version": item.version})


class ConsoleRoleMembers(ConsoleAction):
    @transaction.atomic
    def post(self, request, resource, role_id):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group
        from .admin_console import can
        from .models import OrganizationRole
        names = serializers.ListField(child=serializers.CharField(max_length=150), min_length=1, max_length=100).run_validation(request.data.get("usernames"))
        users = list(get_user_model().objects.filter(username__in=set(names), is_active=True))
        if len(users) != len(set(names)):
            raise ValidationError("Use existing, active usernames. No assignments were changed.")
        if resource == "platform-roles":
            if not can(request.user, Group, "change") or not can(request.user, get_user_model(), "change"):
                raise PermissionDenied()
            role = get_object_or_404(Group.objects.select_for_update(), pk=role_id)
            permissions = {f"{p.content_type.app_label}.{p.codename}" for p in role.permissions.select_related("content_type")}
            if not request.user.is_superuser and (not permissions.issubset(request.user.get_all_permissions()) or any(user.is_superuser for user in users)):
                raise PermissionDenied("These assignments exceed your permissions.")
            role.user_set.add(*users)
        elif resource == "organization-roles":
            require_permission(request.user, "organizationrole", "change")
            require_permission(request.user, "organizationmembership", "change")
            role = get_object_or_404(OrganizationRole, pk=role_id)
            from .organization_roles import assign_role_members
            assign_role_members(role.organization, role, users)
        else:
            raise ValidationError("Choose an organization or platform role.")
        audit(request, "role.members_assigned", metadata={"roleId": str(role.pk), "resource": resource, "count": len(users)})
        return Response({"assigned": len(users)})


class ConsoleTransit(ConsoleAction):
    def get(self, request, project_id):
        require_permission(request.user, "project")
        from .external_sources_admin import TransitStationForm, saved_station_routes
        project = get_object_or_404(Project, pk=project_id)
        routes = saved_station_routes(project)
        form = TransitStationForm(project=project, routes=routes)
        return Response({"routes": routes, "projectVersion": project.updated_at, "revision": form.initial["route_revision"],
            "choices": {kind: [{"value": key, "label": label} for key, label in form.fields[kind].choices] for kind in ("railway", "metro")},
            "selected": {kind: form.initial[kind] for kind in ("railway", "metro")}})

    def post(self, request, project_id):
        require_permission(request.user, "project", "change")
        from .external_sources_admin import refresh_google_routes, save_transit_station_choices
        from .ingestion_runtime import work_lock
        project = get_object_or_404(Project, pk=project_id)
        if request.data.get("action") == "retry-routes":
            if request.data.get("confirm") is not True:
                raise ValidationError("Confirm the provider lookup.")
            reference = get_object_or_404(ReviewTask, project=project, pk=request.data.get("referenceId"))
            radius = serializers.IntegerField(min_value=100, max_value=50000).run_validation(request.data.get("radius", 10000))
            with work_lock("external-sources", project.pk):
                refresh_google_routes(project, request.user, reference.pk, radius)
        else:
            save_transit_station_choices(project, request.user, {"project_version": request.data.get("projectVersion"),
                "route_revision": request.data.get("revision"), "railway": request.data.get("railway", ""), "metro": request.data.get("metro", "")})
        return Response({"status": "updated"})


class ConsoleDocumentRetry(ConsoleAction):
    def post(self, request, document_id):
        require_permission(request.user, "evidencedocument", "change")
        from .views import ReprocessDocumentAdminView
        return ReprocessDocumentAdminView().post(request, document_id)


class ConsoleReplay(ConsoleAction):
    @transaction.atomic
    def post(self, request, message_id):
        require_permission(request.user, "outboxmessage", "change")
        if request.data.get("confirm") is not True:
            raise ValidationError("Confirm replaying this message.")
        from .views import ReplayOutboxAdminView, _idempotent_response, _save_idempotency
        from .models import OutboxMessage
        get_object_or_404(OutboxMessage.objects.select_for_update(), pk=message_id)
        key, existing = _idempotent_response(request, f"console-replay:{message_id}")
        if existing:
            return Response(existing.response_body, status=existing.response_status)
        response = ReplayOutboxAdminView().post(request, message_id)
        _save_idempotency(request.user, key, f"console-replay:{message_id}", response.status_code, response.data)
        return response


class ConsoleBulkProjects(ConsoleAction):
    @transaction.atomic
    def post(self, request):
        require_permission(request.user, "project", "change")
        action = serializers.ChoiceField(choices=["retire", "reanalyse"]).run_validation(request.data.get("action"))
        targets = serializers.ListField(child=serializers.UUIDField(), min_length=1, max_length=100).run_validation(request.data.get("ids"))
        if len(set(targets)) != len(targets) or request.data.get("confirm") is not True:
            raise ValidationError("Choose distinct projects and confirm the action.")
        versions = request.data.get("versions")
        if not isinstance(versions, dict):
            raise ValidationError("Reload the selected project versions.")
        import hashlib
        import json
        from .views import _idempotent_response, _save_idempotency
        fingerprint = hashlib.sha256(json.dumps({"ids": sorted(map(str, targets)), "versions": versions}, sort_keys=True).encode()).hexdigest()
        scope = f"console-bulk:{action}:{fingerprint}"
        key, existing = _idempotent_response(request, scope)
        if existing:
            return Response(existing.response_body, status=existing.response_status)
        projects = list(Project.objects.select_for_update().filter(pk__in=targets).order_by("pk"))
        _, existing = _idempotent_response(request, scope)
        if existing:
            return Response(existing.response_body, status=existing.response_status)
        if len(projects) != len(targets):
            raise ValidationError("A selected project is unavailable.")
        for project in projects:
            if project.updated_at != serializers.DateTimeField().run_validation(versions.get(str(project.pk))):
                raise BuilderConflict("A selected project changed. Reload before retrying.")
        from .project_lifecycle import retire_project
        from .reanalysis import queue_project_reanalysis
        for project in projects:
            if action == "retire":
                retire_project(project.pk, request.user)
            else:
                queue_project_reanalysis(project, request.user)
        body = {"updated": len(projects)}
        _save_idempotency(request.user, key, scope, 200, body)
        return Response(body)
