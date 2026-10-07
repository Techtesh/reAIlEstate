import pytest
from django.contrib import admin
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.urls import reverse
from rest_framework.test import APIClient

from platform_api.builder_access import builder_can_manage
from platform_api.models import Organization, OrganizationMembership, OrganizationRole, Project

pytestmark = pytest.mark.django_db


@pytest.fixture
def roles_workspace(django_user_model, settings):
    settings.ROOT_URLCONF = "builder_service.urls"
    owner = django_user_model.objects.create_user(username="role-owner")
    editor = django_user_model.objects.create_user(username="role-editor")
    first = django_user_model.objects.create_user(username="new-member-one")
    second = django_user_model.objects.create_user(username="new-member-two")
    org = Organization.objects.create(legal_name="Role organization")
    OrganizationMembership.objects.create(organization=org, user=owner, role="owner")
    OrganizationMembership.objects.create(organization=org, user=editor, role="editor")
    client = APIClient()
    client.force_login(owner)
    return client, org, owner, editor, first, second


def role_url(org):
    return f"/api/v1/builder/organizations/{org.pk}/roles"


def test_owner_creates_reusable_role_and_onboards_multiple_members(roles_workspace):
    client, org, owner, editor, first, second = roles_workspace
    result = client.post(role_url(org), {"name": "Project team", "baseRole": "editor"}, format="json")
    assert result.status_code == 201
    role = OrganizationRole.objects.get(pk=result.json()["id"])
    url = role_url(org) + f"/{role.pk}/members"
    response = client.post(url, {"usernames": [first.username, second.username, first.username]}, format="json")
    assert response.status_code == 200
    assert response.json() == {"assigned": 2}
    assert org.memberships.filter(named_role=role).count() == 2
    project = Project.objects.create(organization=org, submitted_name="Role project")
    assert builder_can_manage(first, project)
    client.post(url, {"usernames": [first.username, second.username]}, format="json")
    assert list(org.memberships.filter(named_role=role).values_list("version", flat=True)) == [1, 1]
    assert client.get(role_url(org)).json()["members"][0]["roleName"]


@pytest.mark.parametrize("actor_index", [3, 4])
def test_editor_or_nonmember_cannot_manage_roles(roles_workspace, actor_index):
    client, org, *rest = roles_workspace
    actor = (client, org, *rest)[actor_index]
    client.force_login(actor)
    assert client.get(role_url(org)).status_code == 403
    assert client.post(role_url(org), {"name": "Forbidden", "baseRole": "owner"}, format="json").status_code == 403
    assert not org.roles.exists()


def test_unknown_account_rolls_back_entire_onboarding_batch(roles_workspace):
    client, org, owner, editor, first, second = roles_workspace
    role = OrganizationRole.objects.create(organization=org, name="Team", base_role="editor")
    response = client.post(role_url(org) + f"/{role.pk}/members", {"usernames": [first.username, "unknown-account"]}, format="json")
    assert response.status_code == 400
    assert not org.memberships.filter(user=first).exists()


def test_role_cannot_be_used_across_organizations(roles_workspace):
    client, org, owner, editor, first, second = roles_workspace
    other = Organization.objects.create(legal_name="Other organization")
    role = OrganizationRole.objects.create(organization=other, name="Other owners", base_role="owner")
    assert client.post(role_url(org) + f"/{role.pk}/members", {"usernames": [first.username]}, format="json").status_code == 404
    member = OrganizationMembership(organization=org, user=first, role="owner", named_role=role)
    with pytest.raises(ValidationError):
        member.full_clean()
    # Even a corrupt record created outside form validation must grant no builder access.
    member.save()
    assert not builder_can_manage(first, Project.objects.create(organization=org, submitted_name="Private"))


def test_last_owner_cannot_be_downgraded_by_bulk_assignment(roles_workspace):
    client, org, owner, editor, first, second = roles_workspace
    role = OrganizationRole.objects.create(organization=org, name="Editors", base_role="editor")
    result = client.post(role_url(org) + f"/{role.pk}/members", {"usernames": [owner.username, first.username]}, format="json")
    assert result.status_code == 400
    assert org.memberships.get(user=owner).role == "owner"
    assert not org.memberships.filter(user=first).exists()


def test_role_names_are_case_insensitive_within_an_organization(roles_workspace):
    client, org, *rest = roles_workspace
    assert client.post(role_url(org), {"name": "Team", "baseRole": "editor"}, format="json").status_code == 201
    assert client.post(role_url(org), {"name": "team", "baseRole": "editor"}, format="json").status_code == 400


def test_viewer_template_does_not_grant_editing_access(roles_workspace):
    client, org, owner, editor, first, second = roles_workspace
    role = OrganizationRole.objects.create(organization=org, name="Observers", base_role="viewer")
    # A template overrides the legacy role value.
    OrganizationMembership.objects.create(organization=org, user=first, role="owner", named_role=role)
    assert not builder_can_manage(first, Project.objects.create(organization=org, submitted_name="Read only"))


def test_platform_admin_role_assigns_permissions_to_multiple_accounts(admin_client, django_user_model):
    first = django_user_model.objects.create_user(username="platform-member-one")
    second = django_user_model.objects.create_user(username="platform-member-two")
    permission = Permission.objects.get(codename="view_project", content_type__app_label="platform_api")
    response = admin_client.post(reverse("admin:auth_group_add"), {
        "name": "Data reviewers", "permissions": [permission.pk], "members": [first.pk, second.pk],
        "grant_admin_login": "on", "_save": "Save",
    })
    assert response.status_code == 302
    role = Group.objects.get(name="Data reviewers")
    assert set(role.user_set.values_list("pk", flat=True)) == {first.pk, second.pk}
    first.refresh_from_db()
    assert first.is_staff
    assert first.has_perm("platform_api.view_project")


def test_platform_role_cannot_delegate_permissions_actor_does_not_hold(django_user_model):
    actor = django_user_model.objects.create_user(username="limited-role-admin", is_staff=True)
    request = RequestFactory().get("/admin/auth/group/add/")
    request.user = actor
    form_class = admin.site._registry[Group].get_form(request)
    forbidden = Permission.objects.get(codename="change_user", content_type__app_label="auth")
    form = form_class(data={"name": "Escalation", "permissions": [forbidden.pk], "members": []})
    assert not form.is_valid()
    assert "permissions" in form.errors


def test_organization_role_admin_supports_bulk_onboarding(admin_client, django_user_model):
    owner = django_user_model.objects.create_user(username="admin-org-owner")
    member = django_user_model.objects.create_user(username="admin-org-member")
    org = Organization.objects.create(legal_name="Admin onboarded organization")
    OrganizationMembership.objects.create(organization=org, user=owner, role="owner")
    response = admin_client.post(reverse("admin:platform_api_organizationrole_add"), {
        "organization": org.pk, "name": "Evidence team", "description": "Uploads", "base_role": "editor",
        "members": [member.pk], "_save": "Save",
    })
    assert response.status_code == 302
    assert org.memberships.get(user=member).named_role.name == "Evidence team"


def test_organization_role_admin_rejects_last_owner_downgrade(admin_client, django_user_model):
    owner = django_user_model.objects.create_user(username="admin-last-owner")
    org = Organization.objects.create(legal_name="Protected owners")
    OrganizationMembership.objects.create(organization=org, user=owner, role="owner")
    response = admin_client.post(reverse("admin:platform_api_organizationrole_add"), {
        "organization": org.pk, "name": "Editors", "base_role": "editor", "members": [owner.pk], "_save": "Save",
    })
    assert response.status_code == 200
    assert b"Keep at least one active organization owner" in response.content
    assert not org.roles.exists()


def test_builder_role_mutations_require_csrf(roles_workspace):
    _, org, owner, *_ = roles_workspace
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(owner)
    assert client.get(role_url(org)).status_code == 200
    response = client.post(role_url(org), {"name": "Blocked", "baseRole": "editor"}, format="json")
    assert response.status_code == 403
    assert not org.roles.exists()


def test_admin_cannot_change_access_of_an_assigned_template(admin_client, django_user_model):
    member = django_user_model.objects.create_user(username="assigned-template-member")
    org = Organization.objects.create(legal_name="Fixed template organization")
    role = OrganizationRole.objects.create(organization=org, name="Editors", base_role="editor")
    OrganizationMembership.objects.create(organization=org, user=member, role="editor", named_role=role)
    response = admin_client.post(reverse("admin:platform_api_organizationrole_change", args=[role.pk]), {
        "organization": org.pk, "name": role.name, "base_role": "owner", "members": [], "_save": "Save",
    })
    assert response.status_code == 200
    assert b"Create a new role" in response.content
    role.refresh_from_db()
    assert role.base_role == "editor"
