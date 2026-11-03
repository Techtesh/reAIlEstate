"""Permission-scoped console data. History is read-only; mutations are allowlisted."""
import hashlib
import json

from django import forms
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.forms import AdminPasswordChangeForm, PasswordChangeForm
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import models as m
from .api_common import AdminAPIView
from .builder_views import BuilderConflict
from .services import audit
from .views import SessionLoginView, SessionLogoutView


# Explicit field lists prevent passwords, storage keys and private token material leaking.
RESOURCES = {
    "outbox": (m.OutboxMessage, "event_type schema_version correlation_id occurred_at published_at attempts dead_lettered_at last_error_code available_after", ""),
    "projects": (m.Project, "organization submitted_name locality status maha_rera_id verification_status published_fact_set_version", ""),
    "organizations": (m.Organization, "legal_name status verification_state confirm_name_changes", "legal_name status verification_state confirm_name_changes"),
    "users": (get_user_model(), "username email first_name last_name is_active is_staff is_superuser groups user_permissions", "username email first_name last_name is_active is_staff is_superuser groups user_permissions"),
    "platform-roles": (Group, "name permissions", "name permissions"),
    "organization-roles": (m.OrganizationRole, "organization name description base_role", "organization name description base_role"),
    "memberships": (m.OrganizationMembership, "organization user role named_role status version", "organization user role named_role status"),
    "amenities": (m.Amenity, "taxonomy_code display_name category", "taxonomy_code display_name category"),
    "stations": (m.StationCandidate, "name line kind latitude longitude sequence active needs_review issue coordinate_options", "name line kind latitude longitude sequence active needs_review issue"),
    "documents": (m.EvidenceDocument, "project original_name declared_media_type byte_length scan_status status error_category retention_until", ""),
    "runs": (m.IngestionRun, "project document status pipeline_version extraction_provider embedding_provider started_at completed_at summary error_code", ""),
    "merges": (m.ProjectMerge, "source_project target_project state reason_code notes merged_by applied_at", ""),
    "floor-plans": (m.FloorPlan, "project document page_number plan_scope plan_name display_name active review_state features", ""),
    "assets": (m.FloorPlanAsset, "project document page_number asset_type media_type pixel_width pixel_height status source_bounds", ""),
    "project-amenities": (m.ProjectAmenity, "project amenity availability_state distance_meters travel_minutes evidence_summary", ""),
    "candidates": (m.FactCandidate, "project document field_code typed_value unit page_number confidence review_state", ""),
    "facts": (m.ProjectFact, "project field_code typed_value unit source_class status fact_set_version reviewed_by reviewed_at", ""),
    "review-tasks": (m.ReviewTask, "project task_type priority state assigned_to version candidate resolution completed_at", ""),
    "pages": (m.DocumentLayoutPage, "document page_number state source reason_code notes marked_by", ""),
    "audit": (m.AuditEvent, "actor action entity_type entity_id reason_code correlation_id occurred_at metadata", ""),
    "consents": (m.Consent, "user purpose policy_version granted_at withdrawn_at", ""),
    "requirements": (m.RequirementProfile, "owner status active_version", ""),
    "recommendations": (m.RecommendationSnapshot, "algorithm_version generated_at expires_at excluded_summary", ""),
}

RESOURCE_LABELS = {
    "outbox": "Outbox & replay",
    "projects": "Projects", "organizations": "Organizations", "users": "Accounts", "platform-roles": "Platform roles",
    "organization-roles": "Organization roles", "memberships": "Memberships", "amenities": "Amenity taxonomy", "stations": "Stations",
    "documents": "Documents", "runs": "Extraction runs", "merges": "Merge history", "floor-plans": "Floor plans", "assets": "Media assets",
    "project-amenities": "Amenity review", "candidates": "Extracted facts", "facts": "Reviewed facts", "review-tasks": "Review queue",
    "pages": "Source pages", "audit": "Audit history", "consents": "Consents", "requirements": "Buyer requirements", "recommendations": "Recommendation history",
}


def can(user, model, action):
    return user.is_active and user.is_staff and user.has_perm(f"{model._meta.app_label}.{action}_{model._meta.model_name}")


def row_data(obj, fields):
    result = {"id": str(obj.pk)}
    for name in fields.split():
        field = obj._meta.get_field(name)
        value = getattr(obj, name)
        if field.many_to_many:
            result[name] = list(value.values_list("pk", flat=True))
        elif field.is_relation:
            result[name] = str(value.pk) if value else None
            result[name + "Label"] = str(value) if value else ""
        else:
            result[name] = field.value_from_object(obj)
    if hasattr(obj, "updated_at"):
        result["updated_at"] = obj.updated_at
    result["versionToken"] = hashlib.sha256(json.dumps(result, sort_keys=True, default=str).encode()).hexdigest()
    return result


def resource_form(model, fields, user, data=None, instance=None):
    form = forms.modelform_factory(model, fields=fields.split())(data=data, instance=instance)
    if model is get_user_model() and not user.is_superuser:
        form.fields.pop("is_superuser", None)
    for name in ("permissions", "user_permissions"):
        if name in form.fields and not user.is_superuser:
            allowed = Q(pk__in=[])
            for code in user.get_all_permissions():
                app, codename = code.split(".", 1)
                allowed |= Q(content_type__app_label=app, codename=codename)
            form.fields[name].queryset = Permission.objects.filter(allowed)
    if model is get_user_model() and not user.is_superuser and "groups" in form.fields:
        form.fields["groups"].queryset = Group.objects.filter(pk__in=[group.pk for group in Group.objects.prefetch_related("permissions__content_type")
            if all(f"{p.content_type.app_label}.{p.codename}" in user.get_all_permissions() for p in group.permissions.all())])
    return form


class ConsoleSession(AdminAPIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response({"username": request.user.get_username(), "isSuperuser": request.user.is_superuser,
            "permissions": sorted(request.user.get_all_permissions()),
            "resources": [{"key": key, "label": RESOURCE_LABELS[key], "create": bool(editable and can(request.user, model, "add")),
                           "update": bool(editable and can(request.user, model, "change"))}
                for key, (model, _, editable) in RESOURCES.items() if can(request.user, model, "view") or can(request.user, model, "change")]})


@method_decorator(csrf_protect, name="dispatch")
class ConsoleLogin(SessionLoginView):
    def login_allowed(self, user):
        return super().login_allowed(user) and user.is_staff


class ConsolePassword(AdminAPIView):
    permission_classes = [IsAdminUser]

    @transaction.atomic
    def post(self, request, user_id=None):
        user = request.user
        target = get_object_or_404(get_user_model().objects.select_for_update(), pk=user_id if user_id is not None else user.pk)
        if user_id is not None:
            if not can(user, get_user_model(), "change"):
                raise PermissionDenied()
            if target.is_superuser and not user.is_superuser:
                raise PermissionDenied("Only a superuser can reset a superuser password.")
            if not user.is_superuser and not target.get_all_permissions().issubset(user.get_all_permissions()):
                raise PermissionDenied("This account exceeds your permissions.")
            form = AdminPasswordChangeForm(target, request.data)
        else:
            form = PasswordChangeForm(target, request.data)
        if not form.is_valid():
            raise ValidationError(form.errors)
        form.save()
        if target.pk == user.pk:
            update_session_auth_hash(request, target)
        audit(request, "account.password_changed", metadata={"targetUserId": target.pk})
        return Response({"status": "password_changed"})


class ConsoleResource(AdminAPIView):
    permission_classes = [IsAdminUser]

    def handle_exception(self, exc):
        if isinstance(exc, DjangoValidationError):
            exc = ValidationError(exc.messages)
        return super().handle_exception(exc)

    def config(self, request, resource, action="view"):
        if resource not in RESOURCES:
            from django.http import Http404
            raise Http404()
        model, fields, editable = RESOURCES[resource]
        if not can(request.user, model, action) and not (action == "view" and can(request.user, model, "change")):
            raise PermissionDenied()
        return model, fields, editable

    def get(self, request, resource, object_id=None):
        model, fields, editable = self.config(request, resource)
        queryset = model.objects.all().order_by("pk")
        if model is m.ReviewTask:
            queryset = queryset.order_by("priority", "created_at", "pk")
        elif model is m.AuditEvent:
            queryset = queryset.order_by("-occurred_at", "pk")
        if object_id:
            return Response(row_data(get_object_or_404(queryset, pk=object_id), fields))
        query = request.query_params.get("q", "").strip()[:200]
        if query:
            search = Q(pk__in=[])
            for name in fields.split():
                if isinstance(model._meta.get_field(name), (m.models.CharField, m.models.TextField)):
                    search |= Q(**{name + "__icontains": query})
            queryset = queryset.filter(search)
        if request.query_params.get("project") and any(field.name == "project" for field in model._meta.fields):
            queryset = queryset.filter(project_id=request.query_params["project"])
        if request.query_params.get("organization") and any(field.name == "organization" for field in model._meta.fields):
            queryset = queryset.filter(organization_id=request.query_params["organization"])
        localities = []
        if model is m.Project:
            queryset = queryset.exclude(merge_out__state="applied").exclude(address__has_key="mergedIntoProjectId")
            localities = sorted(set(queryset.exclude(locality="").values_list("locality", flat=True)))
            if request.query_params.get("locality"):
                queryset = queryset.filter(locality=request.query_params["locality"])
        page = forms.IntegerField(min_value=1, max_value=100000).clean(request.query_params.get("page", 1))
        count = queryset.count()
        if model is m.Project:
            import re
            def project_order(item):
                return (item.status in {"retired", "merged", "inactive", "archived"},
                    [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", item.submitted_name)], str(item.pk))
            queryset = sorted(queryset, key=project_order)
        form = resource_form(model, editable, request.user) if editable else None
        schema = []
        if form:
            for name, field in form.fields.items():
                kind = "boolean" if isinstance(field, forms.BooleanField) else "json" if isinstance(field, forms.JSONField) else "select" if isinstance(field, forms.ChoiceField) or isinstance(field, forms.ModelChoiceField) else "number" if isinstance(field, (forms.IntegerField, forms.DecimalField)) else "text"
                entry = {"name": name, "label": str(field.label), "required": field.required, "kind": kind, "multiple": isinstance(field, forms.ModelMultipleChoiceField)}
                if hasattr(field, "queryset"):
                    entry["options"] = [{"value": str(obj.pk), "label": str(obj)} for obj in field.queryset.order_by("pk")[:1000]]
                elif hasattr(field, "choices"):
                    entry["options"] = [{"value": str(key), "label": str(label)} for key, label in field.choices]
                schema.append(entry)
        return Response({"count": count, "page": page, "pageSize": 50, "localities": localities,
            "results": [row_data(obj, fields) for obj in queryset[(page-1)*50:page*50]], "schema": schema,
            "canCreate": bool(editable and can(request.user, model, "add")), "canUpdate": bool(editable and can(request.user, model, "change")),
            "canAct": can(request.user, model, "change"),
            "canDelete": resource in {"organizations", "organization-roles", "platform-roles", "amenities"} and can(request.user, model, "delete")})

    @transaction.atomic
    def post(self, request, resource, object_id=None):
        model, fields, editable = self.config(request, resource, "change" if object_id else "add")
        if not editable:
            raise PermissionDenied("This history is read-only. Use its review or processing workflow.")
        if model is get_user_model() and request.user.is_superuser:
            # Serialize privilege changes before taking an individual account lock.
            list(model.objects.select_for_update().filter(is_superuser=True).order_by("pk"))
        instance = get_object_or_404(model.objects.select_for_update(), pk=object_id) if object_id else None
        if instance and request.data.get("versionToken") != row_data(instance, fields)["versionToken"]:
            raise BuilderConflict("This record changed. Reload before saving.")
        values = request.data.get("values")
        if not isinstance(values, dict) or set(values) - set(editable.split()):
            raise ValidationError("Only the displayed editable fields may be changed.")
        if model is get_user_model():
            if not request.user.is_superuser and "is_superuser" in values:
                raise PermissionDenied("Only a superuser can change superuser access.")
            if instance and instance.is_superuser and not request.user.is_superuser:
                raise PermissionDenied("Only a superuser can change a superuser account.")
            if not request.user.is_superuser and values.get("is_staff") != (instance.is_staff if instance else False):
                raise PermissionDenied("Only a superuser can change administration access.")
            if instance and instance.pk == request.user.pk and (values.get("is_active") is False or values.get("is_staff") is False):
                raise ValidationError("Use a different administrator to revoke your administration access.")
            if instance and instance.is_superuser and values.get("is_superuser") is False and not get_user_model().objects.filter(is_superuser=True, is_active=True, is_staff=True).exclude(pk=instance.pk).exists():
                raise ValidationError("Keep an active superuser administrator.")
            if instance and values.get("is_active") is False:
                from .organization_roles import active_owners
                for organization in m.Organization.objects.select_for_update().filter(pk__in=m.OrganizationMembership.objects.filter(user=instance, status="active").values("organization_id")):
                    if active_owners(organization).filter(user=instance).exists() and not active_owners(organization).exclude(user=instance).exists():
                        raise ValidationError("Assign another active organization owner before deactivating this account.")
                if instance.is_superuser and not get_user_model().objects.filter(is_superuser=True, is_active=True, is_staff=True).exclude(pk=instance.pk).exists():
                    raise ValidationError("Keep an active superuser administrator.")
            if instance and not request.user.is_superuser and not instance.get_all_permissions().issubset(request.user.get_all_permissions()):
                raise PermissionDenied("This account exceeds your permissions.")
        if model is Group and not request.user.is_superuser and instance:
            if instance.user_set.filter(is_superuser=True).exists() or any(f"{p.content_type.app_label}.{p.codename}" not in request.user.get_all_permissions() for p in instance.permissions.select_related("content_type")):
                raise PermissionDenied("This role exceeds your permissions.")
        original = {name: getattr(instance, name, None) for name in ("organization_id", "user_id", "base_role")} if instance else {}
        if instance and model is m.OrganizationRole and instance.memberships.exists():
            original["assigned"] = True
        form = resource_form(model, editable, request.user, values, instance)
        if not form.is_valid():
            raise ValidationError(form.errors)
        if model is m.OrganizationRole and original.get("assigned"):
            if form.cleaned_data["organization"].pk != original["organization_id"] or form.cleaned_data["base_role"] != original["base_role"]:
                raise ValidationError("Create a new role to change organization or access for an assigned role.")
        if model is m.OrganizationMembership:
            from .organization_roles import active_owners
            organization = m.Organization.objects.select_for_update().get(pk=form.cleaned_data["organization"].pk)
            named = form.cleaned_data.get("named_role")
            if named and named.organization_id != organization.pk:
                raise ValidationError("Role must belong to this organization.")
            if instance and (original["organization_id"] != organization.pk or original["user_id"] != form.cleaned_data["user"].pk):
                raise ValidationError("Create a new membership to change its user or organization.")
            next_role = named.base_role if named else form.cleaned_data["role"]
            if instance and active_owners(organization).filter(pk=instance.pk).exists() and (next_role != "owner" or form.cleaned_data["status"] != "active") and not active_owners(organization).exclude(pk=instance.pk).exists():
                raise ValidationError("Keep at least one active organization owner.")
            form.instance.role = next_role
            form.instance.version = (instance.version if instance else 0) + 1
        try:
            saved = form.save(commit=False)
            if model is get_user_model() and instance is None:
                saved.set_unusable_password()
            saved.save(); form.save_m2m()
        except IntegrityError:
            raise ValidationError("A conflicting record already exists.") from None
        audit(request, f"console.{resource}.saved", metadata={"recordId": str(saved.pk)})
        return Response(row_data(saved, fields), status=200 if instance else 201)

    @transaction.atomic
    def delete(self, request, resource, object_id):
        model, fields, _ = self.config(request, resource, "delete")
        if resource not in {"organizations", "organization-roles", "platform-roles", "amenities"}:
            raise PermissionDenied("Use deactivation, revocation or the record's audited workflow.")
        item = get_object_or_404(model.objects.select_for_update(), pk=object_id)
        if request.data.get("confirm") is not True or request.data.get("versionToken") != row_data(item, fields)["versionToken"]:
            raise BuilderConflict("Reload and confirm deletion of this unused record.")
        for relation in model._meta.related_objects:
            accessor = relation.get_accessor_name()
            related = getattr(item, accessor, None)
            if related is not None and hasattr(related, "exists") and related.exists():
                raise ValidationError("This record is in use. Deactivate it or remove its assignments first.")
        audit(request, f"console.{resource}.deleted", metadata={"recordId": str(item.pk)})
        item.delete()
        return Response(status=204)
