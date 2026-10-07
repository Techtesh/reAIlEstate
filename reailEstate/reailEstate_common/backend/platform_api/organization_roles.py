"""Reusable organization roles and atomic bulk onboarding."""

from django.db import transaction
from django.db.models import F, Q
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import Organization, OrganizationMembership, OrganizationRole


def active_owners(organization):
    return organization.memberships.filter(status="active", user__is_active=True).filter(
        Q(named_role__isnull=True, role="owner")
        | Q(named_role__organization_id=F("organization_id"), named_role__base_role="owner")
    )


def require_owner(actor, organization):
    if not actor.is_active or organization.status != "active" or not active_owners(organization).filter(user=actor).exists():
        raise PermissionDenied("Only an active organization owner can manage roles and members.")


@transaction.atomic
def assign_role_members(organization, role, users, *, actor=None):
    organization = Organization.objects.select_for_update().get(pk=organization.pk)
    if actor is not None:
        require_owner(actor, organization)
    if role.organization_id != organization.pk:
        raise ValidationError("Choose a role belonging to this organization.")
    if not users or len(users) > 100 or any(not user.is_active for user in users):
        raise ValidationError("Choose between 1 and 100 active accounts.")
    user_ids = {user.pk for user in users}
    if role.base_role != "owner" and not active_owners(organization).exclude(user_id__in=user_ids).exists():
        if active_owners(organization).exists():
            raise ValidationError("Keep at least one active organization owner.")
    for user in users:
        member, created = OrganizationMembership.objects.get_or_create(
            organization=organization, user=user,
            defaults={"role": role.base_role, "named_role": role, "status": "active"},
        )
        if not created and (member.named_role_id != role.pk or member.status != "active" or member.role != role.base_role):
            member.named_role = role
            member.role = role.base_role
            member.status = "active"
            member.version += 1
            member.save(update_fields=["named_role", "role", "status", "version", "updated_at"])
    return len(user_ids)

