"""Organization-scoped builder portal, separate from platform administration."""

from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .builder_access import builder_memberships
from .domain.layout_titles import validate_layout_display_name
from .layout_names import save_layout_name
from .layout_pricing import pricing_layouts, save_layout_price
from .models import Organization, Project, ReviewTask
from .project_lifecycle import retire_project
from .services import audit, normalize_maha_rera_id
from .views import DeveloperProjectCreateView, SessionLoginView


class BuilderConflict(APIException):
    status_code = 409
    default_detail = "This project changed. Reload it before saving."
    default_code = "builder_conflict"


class StrictInput(serializers.Serializer):
    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise ValidationError({key: "This field cannot be changed here." for key in sorted(unknown)})
        return super().to_internal_value(data)


class AddressInput(StrictInput):
    line1 = serializers.CharField(max_length=255, allow_blank=True, required=False)
    line2 = serializers.CharField(max_length=255, allow_blank=True, required=False)
    city = serializers.CharField(max_length=128, allow_blank=True, required=False)
    pincode = serializers.CharField(max_length=16, allow_blank=True, required=False)
    locality = serializers.CharField(max_length=128, allow_blank=True, required=False)


class ProjectInput(StrictInput):
    submittedName = serializers.CharField(max_length=255)
    mahaReraId = serializers.CharField(max_length=64)
    locality = serializers.CharField(max_length=128, allow_blank=True, required=False)
    address = AddressInput(required=False)
    processingAttested = serializers.BooleanField(required=False)

    def validate_mahaReraId(self, value):
        return normalize_maha_rera_id(value)


class CreateProjectInput(ProjectInput):
    organizationId = serializers.UUIDField()


class UpdateProjectInput(ProjectInput):
    expectedUpdatedAt = serializers.DateTimeField()


class VersionInput(StrictInput):
    expectedUpdatedAt = serializers.DateTimeField()


class LayoutInput(VersionInput):
    name = serializers.CharField(max_length=160, required=False, trim_whitespace=False)
    price = serializers.DecimalField(
        max_digits=16, decimal_places=2, min_value=Decimal("0.01"), allow_null=True, required=False,
    )

    def validate_name(self, value):
        try:
            name = validate_layout_display_name(value)
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        if not name:
            raise ValidationError("Enter a layout name.")
        return name


def project_data(project):
    return {
        "id": str(project.pk), "organizationId": str(project.organization_id),
        "organizationName": project.organization.legal_name,
        "submittedName": project.submitted_name, "mahaReraId": project.maha_rera_id or "",
        "locality": project.locality,
        "address": {key: value for key, value in project.address.items()
                    if key in AddressInput().fields and isinstance(value, str)},
        "processingAttested": project.processing_attested, "status": project.status,
        "verificationStatus": project.verification_status,
        "createdAt": project.created_at, "updatedAt": project.updated_at,
    }


def layout_data(plan):
    return {
        "id": str(plan.pk), "name": plan.display_title, "configurationType": plan.configuration_type,
        "price": str(plan.price) if plan.price is not None else None,
        "carpetAreaSqFt": str(plan.carpet_area_sqft) if plan.carpet_area_sqft is not None else None,
        "updatedAt": plan.updated_at, "reviewState": plan.review_state,
    }


def builder_projects(user):
    return Project.objects.filter(
        organization_id__in=builder_memberships(user).values("organization_id"),
    ).exclude(merge_out__state="applied").exclude(
        address__has_key="mergedIntoProjectId",
    ).select_related("organization").order_by("-updated_at", "pk")


def locked_project(user, project_id, expected):
    project = get_object_or_404(builder_projects(user).select_for_update(of=("self",)), pk=project_id)
    if project.status in {"retired", "merged"}:
        raise BuilderConflict("This project is archived and cannot be changed.")
    if expected is None:
        raise ValidationError({"expectedUpdatedAt": "Reload the project before saving."})
    if project.updated_at != expected:
        raise BuilderConflict()
    return project


def require_new_review(project):
    if project.status == "published":
        project.status = "needs_review"
    ReviewTask.objects.get_or_create(
        project=project, task_type="publication", state="open", defaults={"priority": 2},
    )


class BuilderLoginView(SessionLoginView):
    def login_allowed(self, user):
        if not super().login_allowed(user):
            return False
        if not builder_memberships(user).exists():
            raise PermissionDenied(
                "Your credentials are correct, but builder access is not assigned. "
                "Ask a platform administrator to add an active owner or editor membership "
                "in an active organization.",
                code="builder_membership_required",
            )
        return True


class BuilderView(APIView):
    permission_classes = [IsAuthenticated]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response


class BuilderOrganizationsView(BuilderView):
    def get(self, request):
        return Response([
            {"id": str(member.organization_id), "legalName": member.organization.legal_name, "role": member.role}
            for member in builder_memberships(request.user).select_related("organization").order_by("organization__legal_name", "pk")
        ])


class BuilderProjectsView(BuilderView, DeveloperProjectCreateView):
    def get(self, request):
        return Response([project_data(project) for project in builder_projects(request.user)])

    @transaction.atomic
    def post(self, request):
        data = CreateProjectInput(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        organization = get_object_or_404(
            Organization.objects.select_for_update(),
            pk=values.pop("organizationId"),
            pk__in=builder_memberships(request.user).values("organization_id"),
        )
        locality = values.pop("locality", values.get("address", {}).get("locality", ""))
        values["address"] = {**values.get("address", {}), "locality": locality}
        try:
            with transaction.atomic():
                response = self.create_project(request, organization, values)
        except IntegrityError as exc:
            if Project.objects.filter(maha_rera_id=values["mahaReraId"]).exists():
                raise BuilderConflict("A project with this registration already exists.") from exc
            raise
        if response.status_code != 201:
            raise BuilderConflict("A project with this registration already exists.")
        project = Project.objects.select_related("organization").get(pk=response.data["id"])
        return Response(project_data(project), status=201)


class BuilderProjectView(BuilderView):
    def get(self, request, project_id):
        return Response(project_data(get_object_or_404(builder_projects(request.user), pk=project_id)))

    @transaction.atomic
    def patch(self, request, project_id):
        data = UpdateProjectInput(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        project = locked_project(request.user, project_id, values.pop("expectedUpdatedAt", None))
        if "locality" not in values and "locality" in values.get("address", {}):
            values["locality"] = values["address"]["locality"]
        changed = []
        for key, field in (
            ("submittedName", "submitted_name"), ("mahaReraId", "maha_rera_id"),
            ("locality", "locality"), ("processingAttested", "processing_attested"),
        ):
            if key in values and getattr(project, field) != values[key]:
                setattr(project, field, values[key])
                changed.append(field)
        if "address" in values or "locality" in changed:
            address = dict(project.address)
            if "address" in values:
                address = {key: value for key, value in address.items() if key not in AddressInput().fields}
                address.update(values["address"])
            address["locality"] = project.locality
            if address != project.address:
                project.address = address
                changed.append("address")
        if not changed:
            return Response(project_data(project))
        if "maha_rera_id" in changed:
            if Project.objects.filter(maha_rera_id=project.maha_rera_id).exclude(pk=project.pk).exists():
                raise BuilderConflict("A project with this registration already exists.")
            project.verification_status = "unavailable"
            project.verification_checked_at = None
        require_new_review(project)
        try:
            with transaction.atomic():
                project.save()
        except IntegrityError as exc:
            if Project.objects.filter(maha_rera_id=project.maha_rera_id).exclude(pk=project.pk).exists():
                raise BuilderConflict("A project with this registration already exists.") from exc
            raise
        audit(request, "builder.project_updated", project, metadata={"fields": changed})
        return Response(project_data(project))


class BuilderProjectArchiveView(BuilderView):
    @transaction.atomic
    def post(self, request, project_id):
        data = VersionInput(data=request.data)
        data.is_valid(raise_exception=True)
        project = locked_project(request.user, project_id, data.validated_data["expectedUpdatedAt"])
        project = retire_project(project.pk, request.user, reason_code="BUILDER_ARCHIVE")
        return Response(project_data(project))


class BuilderLayoutsView(BuilderView):
    def get(self, request, project_id):
        project = get_object_or_404(builder_projects(request.user), pk=project_id)
        return Response([layout_data(plan) for plan in pricing_layouts(can_review=True).filter(project=project).order_by("display_name", "pk")])

    @transaction.atomic
    def patch(self, request, project_id, layout_id):
        project = get_object_or_404(builder_projects(request.user).select_for_update(of=("self",)), pk=project_id)
        if project.status in {"retired", "merged"}:
            raise BuilderConflict("This project is archived and cannot be changed.")
        data = LayoutInput(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        plan = get_object_or_404(
            pricing_layouts(can_review=True).filter(project=project).select_for_update(of=("self",)), pk=layout_id,
        )
        if plan.updated_at != values["expectedUpdatedAt"]:
            raise BuilderConflict("This layout changed. Reload it before saving.")
        before = (plan.display_name, plan.price)
        if "name" in values:
            plan = save_layout_name(project, plan.pk, values["name"], request.user, correlation_id=request.correlation_id)
        if "price" in values:
            plan = save_layout_price(project, plan.pk, values["price"], request.user, expected_updated_at=plan.updated_at)
        if before != (plan.display_name, plan.price):
            if plan.review_state == "approved":
                plan.review_state = "needs_review"
                plan.save(update_fields=["review_state", "updated_at"])
            if not ReviewTask.objects.filter(
                project=project, task_type="floor_plan", state="open",
                resolution__floorPlanId=str(plan.pk),
            ).exists():
                ReviewTask.objects.create(project=project, task_type="floor_plan", priority=1, resolution={
                    "floorPlanId": str(plan.pk), "source": "builder_edit", "submittedBy": request.user.pk,
                })
            require_new_review(project)
            project.save(update_fields=["status", "updated_at"])
            audit(request, "builder.layout_updated", plan)
        return Response(layout_data(plan))
