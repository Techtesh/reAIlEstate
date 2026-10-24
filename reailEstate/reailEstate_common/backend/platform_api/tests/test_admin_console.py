import pytest
from django.contrib.auth.models import Group, Permission
from django.test import Client
from rest_framework.test import APIClient

from platform_api.admin_console import RESOURCES
from platform_api.models import Organization, OrganizationMembership, OrganizationRole, Project

pytestmark = pytest.mark.django_db
BASE = "/api/v1/admin/console"


@pytest.mark.parametrize("role", ["admin", "builder", "buyer"])
def test_service_bootstraps_with_its_own_settings_in_a_fresh_process(settings, role):
    import subprocess
    import sys
    result = subprocess.run([sys.executable, str(settings.PROJECT_ROOT / f"{role}Service" / "manage.py"), "check", f"--settings={role}_service.settings"],
        capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def console(settings, django_user_model, tmp_path):
    settings.ROOT_URLCONF = "admin_service.urls"
    settings.SECURE_SSL_REDIRECT = False
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.FRONTEND_DIST = tmp_path / "frontend"
    settings.FRONTEND_DIST.mkdir()
    (settings.FRONTEND_DIST / "index.html").write_text("<html>Custom application</html>")
    user = django_user_model.objects.create_superuser(username="console-root", password="test-console-password")
    client = APIClient(); client.force_login(user)
    return client, user


def test_every_resource_schema_and_read_endpoint_is_valid(console):
    client, _ = console
    for key in RESOURCES:
        response = client.get(f"{BASE}/records/{key}")
        assert response.status_code == 200, (key, response.content)
    data = client.get(BASE + "/session").json()
    assert {row["key"] for row in data["resources"]} == set(RESOURCES)
    user_rows = client.get(BASE + "/records/users").json()["results"]
    assert "password" not in str(user_rows).lower()


def test_console_is_staff_and_permission_scoped(console, django_user_model):
    client, _ = console
    user = django_user_model.objects.create_user(username="limited", is_staff=True)
    client.force_login(user)
    assert client.get(BASE + "/session").json()["resources"] == []
    assert client.get(BASE + "/records/users").status_code == 403
    user.user_permissions.add(Permission.objects.get(content_type__app_label="platform_api", codename="view_organization"))
    assert client.get(BASE + "/records/organizations").status_code == 200
    assert client.post(BASE + "/records/organizations", {"values": {"legal_name": "No"}}, format="json").status_code == 403
    user.is_staff = False; user.save()
    assert client.get(BASE + "/session").status_code == 403


def test_resource_updates_require_current_token_and_history_is_read_only(console):
    client, _ = console
    organization = Organization.objects.create(legal_name="Original")
    url = f"{BASE}/records/organizations/{organization.pk}"
    row = client.get(url).json()
    values = {"legal_name": "Changed", "status": "active", "verification_state": "pending", "confirm_name_changes": True}
    result = client.post(url, {"values": values, "versionToken": row["versionToken"]}, format="json")
    assert result.status_code == 200, result.content
    assert client.post(url, {"values": values, "versionToken": row["versionToken"]}, format="json").status_code == 409
    assert client.post(BASE + "/records/audit", {"values": {}}, format="json").status_code == 403
    assert client.delete(BASE + "/records/audit/anything", {}, format="json").status_code == 403


def test_project_sorting_filters_full_result_set_before_paging(console):
    client, _ = console
    organization = Organization.objects.create(legal_name="Projects")
    for name, status, locality in [("Site 1", "retired", "East"), ("site 10", "published", "West"), ("Site 2", "needs_review", "East"), ("Site 3", "retired", "East")]:
        Project.objects.create(organization=organization, submitted_name=name, status=status, locality=locality)
    url = BASE + "/records/projects"
    assert [r["submittedName"] for r in client.get(url).json()["results"]] == ["Site 2", "site 10", "Site 1", "Site 3"]
    assert [r["submittedName"] for r in client.get(url, {"q": "SITE", "locality": "East"}).json()["results"]] == ["Site 2", "Site 1", "Site 3"]


def test_last_owner_and_assigned_role_are_protected(console, django_user_model):
    client, _ = console
    org = Organization.objects.create(legal_name="Owner checks")
    owner = django_user_model.objects.create_user(username="owner")
    role = OrganizationRole.objects.create(organization=org, name="Owner", base_role="owner")
    member = OrganizationMembership.objects.create(organization=org, user=owner, named_role=role, role="owner")
    url = f"{BASE}/records/memberships/{member.pk}"
    token = client.get(url).json()["versionToken"]
    result = client.post(url, {"versionToken": token, "values": {"organization": str(org.pk), "user": owner.pk, "named_role": str(role.pk), "role": "owner", "status": "suspended"}}, format="json")
    assert result.status_code == 400, result.content
    url = f"{BASE}/records/organization-roles/{role.pk}"
    token = client.get(url).json()["versionToken"]
    result = client.post(url, {"versionToken": token, "values": {"organization": str(org.pk), "name": "Editor", "description": "", "base_role": "editor"}}, format="json")
    assert result.status_code == 400
    role.refresh_from_db(); assert role.base_role == "owner"


def test_staff_cannot_grant_permissions_they_do_not_hold(console, django_user_model):
    client, _ = console
    staff = django_user_model.objects.create_user(username="role-manager", is_staff=True)
    staff.user_permissions.add(Permission.objects.get(content_type__app_label="auth", codename="add_group"))
    client.force_login(staff)
    forbidden = Permission.objects.get(content_type__app_label="auth", codename="change_user")
    response = client.post(BASE + "/records/platform-roles", {"values": {"name": "Escalated", "permissions": [forbidden.pk]}}, format="json")
    assert response.status_code == 400
    assert not Group.objects.filter(name="Escalated").exists()


def test_deletion_checks_dependencies_and_preserves_project_history(console):
    client, _ = console
    organization = Organization.objects.create(legal_name="In use")
    Project.objects.create(organization=organization, submitted_name="Keep evidence")
    url = f"{BASE}/records/organizations/{organization.pk}"
    token = client.get(url).json()["versionToken"]
    assert client.delete(url, {"confirm": True, "versionToken": token}, format="json").status_code == 400
    assert organization.projects.exists()


def test_custom_entry_points_and_legacy_mutations(console):
    client, _ = console
    for path in ["/admin/", "/admin/login/", "/admin/password/", "/admin/queue/", "/admin/records/users/", "/admin/project-workspace/"]:
        response = client.get(path)
        assert response.status_code == 200, path
        assert b"Custom application" in b"".join(response.streaming_content), path
        assert "unsafe-inline" not in response["Content-Security-Policy"]
    response = client.get("/admin/auth/user/")
    assert response.status_code == 302 and response.url == "/admin/records/users/"
    assert client.post("/admin/auth/user/add/", {}).status_code == 405


def test_login_rejects_nonstaff_and_requires_csrf(console, django_user_model):
    _, _ = console
    django_user_model.objects.create_user(username="buyer", password="test-console-password")
    client = Client(enforce_csrf_checks=True)
    assert client.post(BASE + "/login", {"username": "console-root", "password": "test-console-password"}).status_code == 403
    client.get(BASE + "/login")
    token = client.cookies["csrftoken"].value
    assert client.post(BASE + "/login", {"username": "buyer", "password": "test-console-password"}, HTTP_X_CSRFTOKEN=token).status_code == 403
    assert client.post(BASE + "/login", {"username": "console-root", "password": "test-console-password"}, HTTP_X_CSRFTOKEN=token).status_code == 200


def test_account_creation_and_password_setting_do_not_expose_hashes(console, django_user_model):
    client, _ = console
    response = client.post(BASE + "/records/users", {"values": {"username": "new-operator", "email": "", "first_name": "", "last_name": "",
        "is_active": True, "is_staff": True, "is_superuser": False, "groups": [], "user_permissions": []}}, format="json")
    assert response.status_code == 201, response.content
    target = django_user_model.objects.get(username="new-operator")
    assert not target.has_usable_password()
    result = client.post(f"{BASE}/users/{target.pk}/password", {"password1": "Strong-example-operator-938!", "password2": "Strong-example-operator-938!"}, format="json")
    assert result.status_code == 200, result.content
    target.refresh_from_db()
    assert target.check_password("Strong-example-operator-938!")
    assert "password" not in client.get(f"{BASE}/records/users/{target.pk}").json()


def test_last_superuser_cannot_be_demoted(console):
    client, user = console
    row = client.get(f"{BASE}/records/users/{user.pk}").json()
    response = client.post(f"{BASE}/records/users/{user.pk}", {"versionToken": row["versionToken"], "values": {
        "username": user.username, "email": "", "first_name": "", "last_name": "", "is_active": True, "is_staff": True,
        "is_superuser": False, "groups": [], "user_permissions": []}}, format="json")
    assert response.status_code == 400
    user.refresh_from_db(); assert user.is_superuser
