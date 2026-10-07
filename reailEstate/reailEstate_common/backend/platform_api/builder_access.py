"""Builder rights come from active organization memberships, never staff flags."""

from django.db.models import F, Q

from .models import OrganizationMembership


def builder_memberships(user):
    memberships = OrganizationMembership.objects.filter(
        status=OrganizationMembership.Status.ACTIVE, organization__status="active",
    ).filter(
        Q(named_role__isnull=True, role__in=["owner", "editor"])
        | Q(named_role__organization_id=F("organization_id"), named_role__base_role__in=["owner", "editor"])
    ).select_related("named_role")
    if not user or not user.is_authenticated or not user.is_active:
        return memberships.none()
    return memberships.filter(user=user)


def builder_can_manage(user, project):
    return builder_memberships(user).filter(organization_id=project.organization_id).exists()
