"""Organization owner and administrator endpoints for reusable access roles."""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response

from .builder_views import BuilderView, StrictInput
from .models import Organization, OrganizationRole
from .organization_roles import assign_role_members, is_owner, require_role_manager
from .services import audit


class RoleInput(StrictInput):
    name = serializers.CharField(max_length=80)
    description = serializers.CharField(max_length=255, allow_blank=True, default="")
    baseRole = serializers.ChoiceField(choices=["owner", "admin", "editor", "viewer"])


class MemberInput(StrictInput):
    usernames = serializers.ListField(child=serializers.CharField(max_length=150), min_length=1, max_length=100)


class OrganizationRolesView(BuilderView):
    def organization(self, request, organization_id):
        organization = get_object_or_404(Organization, pk=organization_id)
        require_role_manager(request.user, organization)
        return organization

    def get(self, request, organization_id):
        organization = self.organization(request, organization_id)
        return Response({
            "roles": [{"id": str(role.pk), "name": role.name, "description": role.description,
                       "baseRole": role.base_role} for role in organization.roles.all()],
            "members": [{"username": member.user.username, "role": member.effective_role,
                         "roleName": member.named_role.name if member.named_role_id else member.role,
                         "status": member.status} for member in organization.memberships.select_related("user", "named_role").order_by("user__username")],
        })

    @transaction.atomic
    def post(self, request, organization_id):
        organization = self.organization(request, organization_id)
        data = RoleInput(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        if values["baseRole"] == "owner" and not is_owner(request.user, organization):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only organization owners can create owner roles.")
        try:
            with transaction.atomic():
                role = OrganizationRole.objects.create(organization=organization, name=values["name"],
                    description=values["description"], base_role=values["baseRole"])
        except IntegrityError:
            raise serializers.ValidationError({"name": "This organization already has a role with that name."}) from None
        audit(request, "organization.role_created", role)
        return Response({"id": str(role.pk), "name": role.name, "description": role.description,
                         "baseRole": role.base_role}, status=201)


class OrganizationRoleMembersView(OrganizationRolesView):
    http_method_names = ["post", "options"]

    @transaction.atomic
    def post(self, request, organization_id, role_id):
        organization = self.organization(request, organization_id)
        role = get_object_or_404(OrganizationRole, pk=role_id, organization=organization)
        data = MemberInput(data=request.data)
        data.is_valid(raise_exception=True)
        names = set(data.validated_data["usernames"])
        users = list(get_user_model().objects.filter(username__in=names, is_active=True))
        if len(users) != len(names):
            raise serializers.ValidationError({"usernames": "Use existing, active platform usernames. No memberships were changed."})
        count = assign_role_members(organization, role, users, actor=request.user)
        audit(request, "organization.members_onboarded", organization, metadata={"roleId": str(role.pk), "count": count})
        return Response({"assigned": count})
