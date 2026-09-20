"""Builder rights come from active organization memberships, never staff flags."""

from .models import OrganizationMembership


def builder_memberships(user):
    memberships = OrganizationMembership.objects.filter(
        status=OrganizationMembership.Status.ACTIVE, organization__status="active",
        role__in=[OrganizationMembership.Role.OWNER, OrganizationMembership.Role.EDITOR],
    )
    if not user or not user.is_authenticated or not user.is_active:
        return memberships.none()
    return memberships.filter(user=user)


def builder_can_manage(user, project):
    return builder_memberships(user).filter(organization_id=project.organization_id).exists()
