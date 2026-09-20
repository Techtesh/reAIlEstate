import hashlib
from datetime import timedelta
from uuid import uuid4

import pytest
from django import forms
from django.contrib import admin
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient

from platform_api.models import (
    AgentWorkflowRun, AuditEvent, EvidenceDocument, FloorPlan, FloorPlanAsset, IngestionRun,
    Organization, OrganizationMembership, Project, ReviewTask, VerificationSnapshot,
)


pytestmark = pytest.mark.django_db


@pytest.fixture
def workspace(django_user_model, settings):
    settings.SECURE_SSL_REDIRECT = False
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.PROJECT_PUBLIC_PREVIEW_ENABLED = False
    user = django_user_model.objects.create_user(username="builder", password="builder-test-only")
    org = Organization.objects.create(legal_name="Builder one")
    other_org = Organization.objects.create(legal_name="Builder two")
    membership = OrganizationMembership.objects.create(user=user, organization=org, role="owner")
    project = Project.objects.create(
        organization=org, submitted_name="My project", maha_rera_id="P51800000001",
        address={"line1": "Known address", "privateMetadata": "must not appear"},
    )
    foreign = Project.objects.create(organization=other_org, submitted_name="Other private project", maha_rera_id="P51800000002")
    client = APIClient()
    client.force_login(user)
    return client, user, membership, project, foreign


def project_url(project):
    return f"/api/v1/builder/projects/{project.pk}"


def make_layout(project, user):
    document = EvidenceDocument.objects.create(
        project=project, original_name="plan.pdf", object_key=f"test/{uuid4()}",
        declared_media_type="application/pdf", byte_length=1, sha256="0" * 64,
        retention_until=timezone.now() + timedelta(days=7), scan_status="clean", uploaded_by=user,
    )
    ingestion = IngestionRun.objects.create(project=project, document=document)
    run = AgentWorkflowRun.objects.create(project=project, document=document, ingestion_run=ingestion)
    asset = FloorPlanAsset.objects.create(
        project=project, document=document, workflow_run=run, page_number=1,
        object_key=f"test/{uuid4()}.png", byte_length=1, sha256="1" * 64,
        pixel_width=100, pixel_height=100, source_bounds={"category": "layouts"},
    )
    return FloorPlan.objects.create(
        project=project, document=document, workflow_run=run, asset=asset, page_number=1,
        plan_scope="flat", plan_name="Unit A", review_state="approved",
    )


def test_builder_login_requires_membership_csrf_and_is_not_django_admin(workspace, django_user_model):
    _, user, membership, project, _ = workspace
    client = APIClient(enforce_csrf_checks=True)
    url = "/api/v1/auth/builder-login"
    credentials = {"username": user.username, "password": "builder-test-only"}
    assert client.post(url, credentials, format="json").status_code == 403
    assert client.get(url).status_code == 200
    token = client.cookies["csrftoken"].value
    result = client.post(url, credentials, format="json", HTTP_X_CSRFTOKEN=token)
    assert result.status_code == 200 and result.json()["username"] == "builder"
    assert client.get("/admin/").status_code == 302
    user.refresh_from_db()
    assert not user.is_staff and not user.is_superuser
    assert client.patch(project_url(project), {}, format="json").status_code == 403
    assert client.get("/api/v1/builder/organizations").json() == [
        {"id": str(membership.organization_id), "legalName": "Builder one", "role": "owner"},
    ]
    client.logout()
    django_user_model.objects.create_user(username="buyer", password="buyer-test-only")
    client.get(url)
    assert client.post(url, {"username": "buyer", "password": "buyer-test-only"}, format="json",
                       HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value).status_code == 403


def test_correct_password_without_membership_reports_access_problem(workspace):
    client, user, membership, _, _ = workspace
    membership.delete()
    client.logout()
    url = "/api/v1/auth/builder-login"
    denied = client.post(url, {"username": user.username, "password": "builder-test-only"}, format="json")
    assert denied.status_code == 403
    assert "credentials are correct" in denied.json()["errors"]["detail"]
    assert "active owner or editor membership" in denied.json()["errors"]["detail"]
    invalid = client.post(url, {"username": user.username, "password": "not-the-password"}, format="json")
    assert invalid.status_code == 403 and invalid.json()["errors"]["detail"] == "Invalid credentials."
    assert client.get("/api/v1/auth/me").status_code == 403
    OrganizationMembership.objects.create(user=user, organization=membership.organization, role="owner")
    allowed = client.post(url, {"username": user.username, "password": "builder-test-only"}, format="json")
    assert allowed.status_code == 200


def test_organization_and_membership_states_are_enum_dropdowns(workspace):
    _, _, membership, project, _ = workspace
    for model, name in ((Organization, "status"), (Organization, "verification_state"),
                        (OrganizationMembership, "status"), (OrganizationMembership, "role")):
        field = model._meta.get_field(name)
        assert isinstance(field.formfield().widget, forms.Select)
        with pytest.raises(ValidationError):
            field.clean("unknown_value", None)
    assert Organization.Status.ACTIVE == membership.organization.status
    assert Organization.VerificationState.PENDING == membership.organization.verification_state
    assert Project.Status.VERIFYING in Project.Status.values
    project.status = Project.Status.VERIFYING
    project._meta.get_field("status").clean(project.status, project)


def test_user_admin_offers_organization_dropdown_for_builder_membership(workspace, admin_client, admin_user):
    _, user, _, _, _ = workspace
    request = RequestFactory().get("/")
    request.user = admin_user
    user_admin = admin.site._registry[type(user)]
    assert user_admin.get_inline_instances(request, None) == []
    inline = user_admin.get_inline_instances(request, user)[0]
    form = inline.get_formset(request, user).form
    assert isinstance(form.base_fields["organization"].widget.widget, forms.Select)
    assert isinstance(form.base_fields["role"].widget, forms.Select)
    assert isinstance(form.base_fields["status"].widget, forms.Select)
    response = admin_client.get(f"/admin/auth/user/{user.pk}/change/")
    assert response.status_code == 200
    assert b'name="organization_memberships-0-organization"' in response.content
    assert b"Builder one" in response.content


@pytest.mark.parametrize("restriction", ["viewer", "suspended", "inactive_org", "inactive_user"])
def test_disabled_builder_access_cannot_be_reused(workspace, restriction):
    client, user, membership, project, _ = workspace
    if restriction == "viewer":
        membership.role = "viewer"
        membership.save()
    elif restriction == "suspended":
        membership.status = "suspended"
        membership.save()
    elif restriction == "inactive_org":
        membership.organization.status = "suspended"
        membership.organization.save()
    else:
        user.is_active = False
        user.save()
    client.logout()
    assert client.post("/api/v1/auth/builder-login", {
        "username": "builder", "password": "builder-test-only",
    }, format="json").status_code == 403
    if restriction != "inactive_user":
        client.force_login(user)
        assert client.get("/api/v1/builder/projects").json() == []
        assert client.get(project_url(project)).status_code == 404
        assert client.post(f"/api/v1/developer-projects/{project.pk}/documents:upload-intent", {}, format="json").status_code == 403


@pytest.mark.parametrize("role", ["owner", "editor"])
def test_builder_projects_are_org_scoped_and_membership_revocation_is_immediate(workspace, role):
    client, user, membership, project, foreign = workspace
    membership.role = role
    membership.save()
    response = client.get("/api/v1/builder/projects")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [str(project.pk)]
    assert "privateMetadata" not in str(response.json())
    assert response["Cache-Control"] == "private, no-store"
    for path in (project_url(foreign), project_url(foreign) + "/layouts"):
        assert client.get(path).status_code == 404
    assert client.patch(project_url(foreign), {
        "submittedName": "Stolen", "expectedUpdatedAt": foreign.updated_at.isoformat(),
    }, format="json").status_code == 404
    assert client.post(project_url(foreign) + ":archive", {
        "expectedUpdatedAt": foreign.updated_at.isoformat(),
    }, format="json").status_code == 404
    membership.status = "revoked"
    membership.save()
    assert client.get("/api/v1/builder/projects").json() == []
    assert client.patch(project_url(project), {
        "submittedName": "Still editing", "expectedUpdatedAt": project.updated_at.isoformat(),
    }, format="json").status_code == 404
    foreign.refresh_from_db()
    assert foreign.submitted_name == "Other private project"


def test_builder_creates_only_in_assigned_org_and_duplicate_does_not_disclose_foreign_project(workspace):
    client, _, membership, _, foreign = workspace
    payload = {
        "organizationId": str(membership.organization_id), "submittedName": "New project",
        "mahaReraId": "p51800000003", "locality": "Thane", "address": {"line1": "Builder address"},
        "processingAttested": True,
    }
    result = client.post("/api/v1/builder/projects", payload, format="json")
    assert result.status_code == 201, result.content
    body = result.json()
    created = Project.objects.get(pk=body["id"])
    assert created.organization_id == membership.organization_id
    assert created.status != "published" and not created.published_fact_set_version
    assert created.maha_rera_id == "P51800000003" and created.locality == "Thane"
    assert created.address["locality"] == "Thane" and created.processing_attested
    assert created.verification_snapshots.filter(match_result="unavailable").exists()
    assert AuditEvent.objects.filter(action="project.created", entity_id=created.pk).exists()
    assert client.post("/api/v1/builder/projects", {**payload, "organizationId": str(foreign.organization_id)}, format="json").status_code == 404
    duplicate = client.post("/api/v1/builder/projects", {**payload, "mahaReraId": foreign.maha_rera_id}, format="json")
    assert duplicate.status_code == 409
    assert str(foreign.pk) not in str(duplicate.json()) and foreign.submitted_name not in str(duplicate.json())
    legacy = client.post(f"/api/v1/developer-organizations/{foreign.organization_id}/projects", payload, format="json")
    assert legacy.status_code == 403


def test_builder_with_multiple_orgs_and_staff_flag_remains_membership_scoped(workspace):
    client, user, _, project, foreign = workspace
    user.is_staff = True
    user.save()
    assert client.get(project_url(foreign)).status_code == 404
    OrganizationMembership.objects.create(user=user, organization=foreign.organization, role="editor")
    response = client.get("/api/v1/builder/projects")
    assert {item["id"] for item in response.json()} == {str(project.pk), str(foreign.pk)}
    assert len(client.get("/api/v1/builder/organizations").json()) == 2


def test_anonymous_builder_requests_are_denied(workspace):
    client, _, _, project, _ = workspace
    client.logout()
    for path in ("/api/v1/builder/organizations", "/api/v1/builder/projects",
                 project_url(project), project_url(project) + "/layouts"):
        assert client.get(path).status_code in {401, 403}
    assert client.post("/api/v1/builder/projects", {}, format="json").status_code in {401, 403}


def test_project_update_keeps_address_locality_in_sync(workspace):
    client, _, _, project, _ = workspace
    for fields, expected in (
        ({"locality": "Thane"}, "Thane"),
        ({"address": {"line1": "New street"}}, "Thane"),
        ({"address": {"locality": "Majiwada"}}, "Majiwada"),
        ({"locality": "", "address": {"locality": "Ignored"}}, ""),
    ):
        response = client.patch(project_url(project), {
            **fields, "expectedUpdatedAt": project.updated_at.isoformat(),
        }, format="json")
        assert response.status_code == 200, response.content
        project.refresh_from_db()
        assert project.locality == expected and project.address["locality"] == expected
        assert project.address["privateMetadata"] == "must not appear"
        assert "privateMetadata" not in response.json()["address"]


@pytest.mark.parametrize("fields", [
    {"price": "0"}, {"price": "-1"}, {"price": "1.001"}, {"name": "   "},
])
def test_invalid_builder_layout_values_leave_layout_unchanged(workspace, fields):
    client, user, _, project, _ = workspace
    layout = make_layout(project, user)
    response = client.patch(project_url(project) + f"/layouts/{layout.pk}", {
        **fields, "expectedUpdatedAt": layout.updated_at.isoformat(),
    }, format="json")
    assert response.status_code == 400
    layout.refresh_from_db()
    assert layout.price is None and layout.display_name == "" and layout.review_state == "approved"


@pytest.mark.parametrize("payload", [
    {"status": "published"}, {"organizationId": str(uuid4())}, {"publishedFactSetVersion": 10},
    {"verificationStatus": "verified"}, {"address": {"mergedIntoProjectId": str(uuid4())}},
    {"address": {"privateMetadata": "override"}}, {"submittedName": ""}, {"mahaReraId": "bad"},
])
def test_builder_cannot_mass_assign_authority_or_invalid_metadata(workspace, payload):
    client, _, _, project, _ = workspace
    response = client.patch(project_url(project), {**payload, "expectedUpdatedAt": project.updated_at.isoformat()}, format="json")
    assert response.status_code == 400
    project.refresh_from_db()
    assert project.status == "draft" and project.submitted_name == "My project"


def test_project_update_uses_optimistic_concurrency_and_requires_new_review(workspace):
    client, _, _, project, _ = workspace
    project.status = "published"
    project.save()
    version = project.updated_at.isoformat()
    assert client.patch(project_url(project), {"submittedName": "No version"}, format="json").status_code == 400
    result = client.patch(project_url(project), {
        "submittedName": "Renamed", "address": {"line1": "New address"}, "expectedUpdatedAt": version,
    }, format="json")
    assert result.status_code == 200, result.content
    assert result.json()["status"] == "needs_review"
    assert client.patch(project_url(project), {
        "submittedName": "Stale edit", "expectedUpdatedAt": version,
    }, format="json").status_code == 409
    project.refresh_from_db()
    assert project.submitted_name == "Renamed"
    assert project.address["privateMetadata"] == "must not appear"
    assert project.review_tasks.filter(task_type="publication", state="open").exists()


def test_builder_can_edit_only_own_layouts_and_cannot_approve(workspace):
    client, user, _, project, foreign = workspace
    project.status = "published"
    project.save()
    layout = make_layout(project, user)
    other_layout = make_layout(foreign, user)
    url = project_url(project) + f"/layouts/{layout.pk}"
    version = layout.updated_at.isoformat()
    response = client.patch(url, {"name": "Unit A East", "price": "12000000.50", "expectedUpdatedAt": version}, format="json")
    assert response.status_code == 200, response.content
    assert response.json()["price"] == "12000000.50" and response.json()["reviewState"] != "approved"
    assert client.patch(url, {"price": "9000000", "expectedUpdatedAt": version}, format="json").status_code == 409
    assert client.patch(project_url(project) + f"/layouts/{other_layout.pk}", {
        "name": "Stolen", "expectedUpdatedAt": other_layout.updated_at.isoformat(),
    }, format="json").status_code == 404
    layout.refresh_from_db()
    assert layout.plan_name == "Unit A" and layout.display_name == "Unit A East"
    repeat = client.patch(url, {
        "price": "12500000", "expectedUpdatedAt": layout.updated_at.isoformat(),
    }, format="json")
    assert repeat.status_code == 200, repeat.content
    layout.refresh_from_db()
    assert client.patch(url, {"reviewState": "approved", "expectedUpdatedAt": layout.updated_at.isoformat()}, format="json").status_code == 400
    assert client.post(f"/api/v1/review/floor-plans/{layout.pk}:resolve", {"decision": "approved"}, format="json").status_code == 403
    assert client.post(f"/api/v1/review/projects/{project.pk}:publish", {}, format="json").status_code == 403
    # A separately assigned reviewer role must still not permit self-publication.
    user.groups.add(Group.objects.get_or_create(name="reviewer")[0])
    assert client.post(f"/api/v1/review/projects/{project.pk}:publish", {},
                       format="json", HTTP_IDEMPOTENCY_KEY=str(uuid4())).status_code == 403
    project.refresh_from_db()
    assert project.status == "needs_review"
    assert ReviewTask.objects.filter(project=project, task_type="floor_plan", state="open").exists()


def test_builder_org_administration_remains_platform_only(workspace):
    client, _, membership, _, _ = workspace
    assert client.post("/api/v1/developer-organizations/", {"legalName": "Claimed company"}, format="json").status_code == 403
    assert client.patch(f"/api/v1/developer-organizations/{membership.organization_id}/", {
        "status": "active", "legalName": "Hijacked",
    }, format="json").status_code == 403
    assert client.delete(f"/api/v1/developer-organizations/{membership.organization_id}/").status_code == 403


def test_builder_archive_preserves_evidence_and_blocks_further_edits(workspace):
    client, user, _, project, _ = workspace
    layout = make_layout(project, user)
    result = client.post(project_url(project) + ":archive", {"expectedUpdatedAt": project.updated_at.isoformat()}, format="json")
    assert result.status_code == 200 and result.json()["status"] == "retired"
    assert FloorPlan.objects.filter(pk=layout.pk).exists()
    assert EvidenceDocument.objects.filter(pk=layout.document_id).exists()
    assert client.patch(project_url(project), {
        "submittedName": "Reactivate", "expectedUpdatedAt": result.json()["updatedAt"],
    }, format="json").status_code == 409
    assert client.post(f"/api/v1/developer-projects/{project.pk}:submit-review", {}, format="json").status_code == 400
    assert client.post(f"/api/v1/developer-projects/{project.pk}/documents:upload-intent", {}, format="json").status_code == 400
    assert client.post(f"/api/v1/developer-projects/{project.pk}:reanalyse", {}, format="json").status_code == 400


@pytest.mark.parametrize("document_status", ["awaiting_upload", "uploaded", "quarantined", "extracting", "needs_review", "rejected"])
def test_repeated_upload_intent_handles_existing_project_document(workspace, document_status):
    client, user, _, project, _ = workspace
    document = EvidenceDocument.objects.create(
        project=project, uploaded_by=user, original_name="original.pdf", object_key=f"test/{uuid4()}",
        declared_media_type="application/pdf", byte_length=14570913, sha256="a" * 64,
        retention_until=timezone.now() + timedelta(days=7), status=document_status,
    )
    response = client.post(f"/api/v1/developer-projects/{project.pk}/documents:upload-intent", {
        "filename": "renamed.pdf", "declaredMediaType": "application/pdf",
        "byteLength": document.byte_length, "sha256": document.sha256,
    }, format="json")
    if document_status == "awaiting_upload":
        assert response.status_code == 200
        assert response.json()["documentId"] == str(document.pk)
    else:
        assert response.status_code == 409
        assert response.json()["errors"]["code"] == "DOCUMENT_ALREADY_EXISTS"
        assert "original.pdf" in response.json()["errors"]["detail"]
        assert document_status in response.json()["errors"]["detail"]
    assert EvidenceDocument.objects.filter(project=project).count() == 1
    document.refresh_from_db()
    assert document.status == document_status and document.original_name == "original.pdf"


def test_builder_upload_and_submit_flow_is_scoped_and_queues_only(workspace, monkeypatch):
    from platform_api import views

    client, user, _, project, foreign = workspace
    queued = []
    monkeypatch.setattr(views, "enqueue_document", lambda document, *args, **kwargs: queued.append(document.pk))
    monkeypatch.setattr(views, "_requested_vision_selection", lambda data: {})
    project.processing_attested = True
    project.save()
    VerificationSnapshot.objects.create(
        project=project, provider="maharera", retrieved_at=timezone.now(), snapshot_hash="0" * 64,
        match_result="unavailable",
    )
    content = b"%PDF-1.4\n%%EOF\n"
    intent = {"filename": "brochure.pdf", "declaredMediaType": "application/pdf",
              "byteLength": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    assert client.post(f"/api/v1/developer-projects/{foreign.pk}/documents:upload-intent", intent, format="json").status_code == 403
    result = client.post(f"/api/v1/developer-projects/{project.pk}/documents:upload-intent", intent, format="json")
    assert result.status_code == 201, result.content
    payload = result.json()
    assert client.put(payload["uploadAction"], content, content_type="application/pdf",
                      HTTP_CONTENT_DISPOSITION='attachment; filename="brochure.pdf"').status_code == 204
    complete = f"/api/v1/developer-projects/{project.pk}/documents/{payload['documentId']}:complete"
    key = str(uuid4())
    assert client.post(complete, {}, format="json", HTTP_IDEMPOTENCY_KEY=key).status_code == 202
    assert client.post(complete, {}, format="json", HTTP_IDEMPOTENCY_KEY=key).status_code == 202
    assert len(queued) == 1
    assert client.get(f"/api/v1/developer-projects/{foreign.pk}/documents").status_code == 403
    assert client.get(f"/api/v1/developer-projects/{project.pk}/documents").json()[0]["originalName"] == "brochure.pdf"
    assert client.post(f"/api/v1/developer-projects/{project.pk}:submit-review", {}, format="json").status_code == 202
    assert project.review_tasks.filter(task_type="publication", state="open").exists()
