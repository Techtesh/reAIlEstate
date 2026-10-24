"""Admin and builder share the editor without sharing authorization scopes."""
import hashlib
from uuid import uuid4

import pytest
from django.contrib.auth.models import Permission
from django.test import override_settings
from django.urls import resolve, Resolver404
from rest_framework.test import APIClient

from platform_api.models import EvidenceDocument, LayoutCropDraft, Organization, OrganizationMembership, OutboxMessage, Project, VerificationSnapshot
from .test_crop_from_page import page_source, REGION


pytestmark = pytest.mark.django_db
PREFIX = "/api/v1/admin/project-workspace"


@pytest.fixture
def workspace(settings, django_user_model, tmp_path):
    settings.ROOT_URLCONF = "admin_service.urls"
    settings.SECURE_SSL_REDIRECT = False
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.FRONTEND_DIST = tmp_path / "frontend"
    settings.FRONTEND_DIST.mkdir()
    (settings.FRONTEND_DIST / "index.html").write_text("<html>Shared editor</html>", encoding="utf-8")
    user = django_user_model.objects.create_user(username="platform-editor", is_staff=True)
    user.user_permissions.add(*Permission.objects.filter(
        content_type__app_label="platform_api", codename__in=["change_project", "add_project"],
    ))
    org = Organization.objects.create(legal_name="Managed organization")
    project = Project.objects.create(organization=org, submitted_name="Existing project")
    client = APIClient()
    client.force_login(user)
    return client, user, org, project


def test_admin_new_project_uses_shared_creation_and_verification(workspace):
    client, user, org, _ = workspace
    assert not OrganizationMembership.objects.filter(user=user).exists()
    response = client.get("/admin/platform_api/project/add/")
    assert response.status_code == 302
    assert response["Location"] == "/admin/project-workspace/?new=1"
    result = client.post(PREFIX + "/builder/projects", {
        "organizationId": str(org.pk), "submittedName": "Admin Gardens",
        "mahaReraId": "P51800000091", "locality": "Powai", "processingAttested": True,
    }, format="json")
    assert result.status_code == 201, result.content
    project = Project.objects.get(pk=result.json()["id"])
    assert project.status == "verifying"
    assert VerificationSnapshot.objects.filter(project=project, match_result="unavailable").exists()
    assert client.get(PREFIX + f"/builder/projects/{project.pk}").status_code == 200


def test_admin_upload_round_trip_is_durable_and_does_not_process_inline(workspace, monkeypatch):
    client, user, _, project = workspace
    from platform_api import workers
    monkeypatch.setattr(workers, "process_document", lambda *a, **kw: pytest.fail("No inline processing"))
    content = b"Private project evidence"
    url = PREFIX + f"/developer-projects/{project.pk}/documents"
    result = client.post(url + ":upload-intent", {
        "filename": "evidence.txt", "declaredMediaType": "text/plain",
        "byteLength": len(content), "sha256": hashlib.sha256(content).hexdigest(),
    }, format="json")
    assert result.status_code == 201, result.content
    intent = result.json()
    assert intent["uploadAction"].startswith(PREFIX + "/developer-projects/")
    uploaded = client.put(intent["uploadAction"], content, content_type="text/plain",
                          HTTP_CONTENT_DISPOSITION='attachment; filename="evidence.txt"')
    assert uploaded.status_code == 204, uploaded.content
    key = str(uuid4())
    completed = client.post(url + f"/{intent['documentId']}:complete", {}, format="json", HTTP_IDEMPOTENCY_KEY=key)
    assert completed.status_code == 202, completed.content
    replay = client.post(url + f"/{intent['documentId']}:complete", {}, format="json", HTTP_IDEMPOTENCY_KEY=key)
    assert replay.status_code == 202 and replay.json() == completed.json()
    document = EvidenceDocument.objects.get(pk=intent["documentId"])
    assert document.uploaded_by == user and document.status == "quarantined" and document.scan_status == "pending"
    assert OutboxMessage.objects.filter(event_type="document.processing.requested").count() == 1
    assert client.get(url).status_code == 200


@pytest.mark.parametrize("kind", ["buyer", "builder", "staff"])
def test_admin_workspace_requires_explicit_project_permission(workspace, django_user_model, kind):
    client, _, org, project = workspace
    user = django_user_model.objects.create_user(username=kind, is_staff=kind == "staff")
    if kind == "builder":
        OrganizationMembership.objects.create(user=user, organization=org, role="owner")
    client.force_login(user)
    # The public shell contains no private records; the APIs enforce access.
    assert client.get("/admin/project-workspace/").status_code == 200
    assert client.get(PREFIX + "/builder/organizations").status_code == 403
    assert client.get(PREFIX + f"/builder/projects/{project.pk}/media").status_code == 403
    assert client.post(PREFIX + f"/developer-projects/{project.pk}/documents:upload-intent", {}, format="json").status_code == 403


def test_admin_creation_still_requires_add_permission(workspace):
    client, user, org, _ = workspace
    user.user_permissions.remove(Permission.objects.get(content_type__app_label="platform_api", codename="add_project"))
    assert client.get(PREFIX + "/builder/organizations").json()[0]["canCreateProject"] is False
    assert client.post(PREFIX + "/builder/projects", {
        "organizationId": str(org.pk), "submittedName": "Forbidden",
        "mahaReraId": "P51800000092",
    }, format="json").status_code == 403


@pytest.mark.django_db(transaction=True)
def test_admin_crops_require_layout_permission_and_never_builder_membership(workspace, page_source):
    client, user, _, _ = workspace
    project, document, _, *_ = page_source
    url = PREFIX + f"/builder/projects/{project.pk}/media"
    body = {"documentId": str(document.pk), "pageNumber": 2, "sourceSha256": document.sha256,
            "regions": [REGION], "requestId": str(uuid4())}
    assert client.post(url + "/crops", body, format="json").status_code == 403
    assert not LayoutCropDraft.objects.exists()
    user.user_permissions.add(Permission.objects.get(content_type__app_label="platform_api", codename="change_floorplan"))
    result = client.post(url + "/crops", body, format="json")
    assert result.status_code == 202, result.content
    assert not OrganizationMembership.objects.filter(user=user).exists()
    assert LayoutCropDraft.objects.get().owner == user
    assert not OutboxMessage.objects.exists()
    gallery = client.get(url)
    assert gallery.status_code == 200
    draft = gallery.json()["drafts"][0]
    assert draft["imageUrl"].startswith(PREFIX)
    preview = client.get(draft["imageUrl"])
    assert preview.status_code == 200
    preview.close()
    replay = client.post(url + "/crops", body, format="json")
    assert replay.json() == result.json() and LayoutCropDraft.objects.count() == 1


def test_admin_editor_has_csrf_and_csp(workspace):
    _, user, _, project = workspace
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(user)
    page = client.get("/admin/project-workspace/")
    assert page.status_code == 200
    assert "script-src 'self'" in page["Content-Security-Policy"]
    assert "csrftoken" in client.cookies
    url = PREFIX + f"/developer-projects/{project.pk}/documents:upload-intent"
    assert client.post(url, {}, format="json").status_code == 403
    # A valid CSRF token reaches input validation, instead of bypassing it.
    assert client.post(url, {}, format="json", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value).status_code == 400


def test_admin_aliases_do_not_grant_builder_or_buyer_service_access(workspace):
    client, _, _, project = workspace
    with override_settings(ROOT_URLCONF="builder_service.urls"):
        assert client.get("/api/v1/builder/projects").json() == []
    with pytest.raises(Resolver404):
        resolve(PREFIX + f"/builder/projects/{project.pk}/media", urlconf="buyer_service.urls")
    with pytest.raises(Resolver404):
        resolve(PREFIX + f"/builder/projects/{project.pk}/media", urlconf="builder_service.urls")


def test_work_lock_reentrancy_preserves_cross_thread_exclusion(settings):
    from concurrent.futures import ThreadPoolExecutor
    from django.db import connections
    from platform_api.ingestion_runtime import work_lock, WorkAlreadyClaimed

    identifier = str(uuid4())

    def competitor():
        try:
            with work_lock("workspace-test", identifier):
                return True
        except WorkAlreadyClaimed:
            return False
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with work_lock("workspace-test", identifier):
            with work_lock("workspace-test", identifier):
                assert executor.submit(competitor).result(timeout=5) is False
            assert executor.submit(competitor).result(timeout=5) is False
        assert executor.submit(competitor).result(timeout=5) is True


def test_admin_project_edits_keep_versions_and_review_invalidation(workspace):
    client, _, _, project = workspace
    project.status = "published"
    project.save()
    path = PREFIX + f"/builder/projects/{project.pk}"
    version = client.get(path).json()["updatedAt"]
    body = {"submittedName": "Edited by admin", "expectedUpdatedAt": version}
    changed = client.patch(path, body, format="json")
    assert changed.status_code == 200, changed.content
    project.refresh_from_db()
    assert project.status == "needs_review"
    assert client.patch(path, body, format="json").status_code == 409


def test_admin_crop_drafts_can_attach_through_same_queue(workspace, page_source):
    client, user, _, _ = workspace
    project, document, _, *_ = page_source
    user.user_permissions.add(Permission.objects.get(content_type__app_label="platform_api", codename="change_floorplan"))
    path = PREFIX + f"/builder/projects/{project.pk}/media"
    staged = client.post(path + "/crops", {
        "documentId": str(document.pk), "pageNumber": 2, "sourceSha256": document.sha256,
        "regions": [REGION], "requestId": str(uuid4()),
    }, format="json")
    assert staged.status_code == 202, staged.content
    body = {"action": "attach", "draftIds": staged.json()["draftIds"], "confirm": True,
            "requestId": str(uuid4()), "visionProfileId": "test"}
    attached = client.post(path + "/drafts", body, format="json")
    assert attached.status_code == 202, attached.content
    assert len(attached.json()["planIds"]) == 1
    assert not LayoutCropDraft.objects.filter(owner=user).exists()
    assert OutboxMessage.objects.filter(event_type="floor_plan.crop_evaluation.requested").count() == 1
    assert client.post(path + "/drafts", body, format="json").json() == attached.json()
    assert OutboxMessage.objects.count() == 1


def test_existing_admin_project_links_to_shared_editor(workspace):
    client, _, org, project = workspace
    response = client.get(f"/admin/platform_api/project/{project.pk}/change/")
    assert response.status_code == 302
    assert response.url == f"/admin/records/projects/?record={project.pk}"
    assert client.get(f"/api/v1/admin/console/records/projects/{project.pk}").json()["organization"] == str(org.pk)


def test_admin_creation_rejects_inactive_organization(workspace):
    client, _, org, _ = workspace
    org.status = "suspended"
    org.save()
    response = client.post(PREFIX + "/builder/projects", {
        "organizationId": str(org.pk), "submittedName": "Inactive organization",
        "mahaReraId": "P51800000093",
    }, format="json")
    assert response.status_code == 404
    assert not Project.objects.filter(maha_rera_id="P51800000093").exists()


def test_admin_media_tools_respect_model_permissions_and_keep_project_context(workspace):
    client, user, _, project = workspace
    url = PREFIX + f"/builder/projects/{project.pk}/media"
    tools = client.get(url).json()["adminTools"]
    assert {tool["label"] for tool in tools} == {
        "Project administration", "Review source references and location",
    }
    user.user_permissions.add(*Permission.objects.filter(
        content_type__app_label="platform_api", codename__in=[
            "change_floorplan", "change_floorplanasset", "change_factcandidate",
            "change_reviewtask", "change_evidencedocument", "change_projectfact",
            "change_projectamenity",
        ],
    ))
    tools = client.get(url).json()["adminTools"]
    assert len(tools) == 9
    for tool in tools:
        assert str(project.pk) in tool["url"]
        response = client.get(tool["url"])
        assert response.status_code == 200, (tool, response.status_code)


def test_admin_tools_never_grant_nonstaff_access(workspace):
    from platform_api.project_workspace import admin_media_tools
    client, user, _, project = workspace
    user.is_staff = False
    user.save(update_fields=["is_staff"])
    assert admin_media_tools(user, project) == []
    assert client.get(PREFIX + f"/builder/projects/{project.pk}/media").status_code == 403


def test_selected_plan_extraction_tools_require_floorplan_permission(workspace, page_source):
    from platform_api.project_workspace import admin_media_tools
    _, user, _, _ = workspace
    from platform_api.models import FloorPlan
    project, document, *_ = page_source
    plan = FloorPlan(project=project, document=document, page_number=1)
    assert admin_media_tools(user, project, plan) == []
    user.user_permissions.add(Permission.objects.get(
        content_type__app_label="platform_api", codename="change_floorplan"))
    user = type(user).objects.get(pk=user.pk)
    tools = admin_media_tools(user, project, plan)
    assert len(tools) == 2
    assert tools[0]["url"].startswith("/admin/project-workspace/?")
    assert f"plan={plan.pk}" in tools[0]["url"]


def test_admin_amenity_correction_preserves_evidence_and_audits_review(workspace):
    from platform_api.models import Amenity, ProjectAmenity, AuditEvent
    client, user, _, project = workspace
    amenity = Amenity.objects.create(taxonomy_code="gym", display_name="Gym", category="health")
    claim = ProjectAmenity.objects.create(project=project, amenity=amenity,
        evidence_summary={"source": "brochure", "reviewState": "unreviewed"})
    url = f"/api/v1/admin/projects/{project.pk}/review"
    assert client.post(url, {"kind": "amenity", "id": str(claim.pk), "decision": "accept", "reason": "Verified"}, format="json").status_code == 403
    user.user_permissions.add(Permission.objects.get(
        content_type__app_label="platform_api", codename="change_projectamenity"))
    assert client.get(url).status_code == 200
    response = client.post(url, {"kind": "amenity", "id": str(claim.pk), "decision": "accept",
        "availability_state": "available", "distance_meters": 10,
        "expectedUpdatedAt": claim.updated_at.isoformat(),
        "travel_minutes": 1, "reason": "Verified against brochure"}, format="json")
    assert response.status_code == 200, response.content
    claim.refresh_from_db()
    assert claim.evidence_summary["source"] == "brochure"
    assert claim.evidence_summary["reviewState"] == "accepted"
    assert claim.distance_meters == 10
    assert AuditEvent.objects.filter(action="project.amenity_reviewed", entity_id=claim.pk).exists()
    stale = client.post(url, {"kind": "amenity", "id": str(claim.pk), "decision": "reject",
        "expectedUpdatedAt": "2000-01-01T00:00:00+00:00", "reason": "Stale correction"}, format="json")
    assert stale.status_code == 409 and b"This amenity changed" in stale.content
    claim.refresh_from_db()
    assert claim.evidence_summary["reviewState"] == "accepted"
    project.status = "retired"
    project.save(update_fields=["status"])
    retired = client.post(url, {"kind": "amenity", "id": str(claim.pk), "decision": "reject",
        "expectedUpdatedAt": claim.updated_at.isoformat(), "reason": "Cannot change retired project"}, format="json")
    assert retired.status_code == 400 and b"read-only" in retired.content
    assert AuditEvent.objects.filter(action="project.amenity_reviewed", entity_id=claim.pk).count() == 1


def test_admin_cannot_accept_own_amenity_claim(workspace):
    from platform_api.models import Amenity, ProjectAmenity
    client, user, _, project = workspace
    user.user_permissions.add(Permission.objects.get(
        content_type__app_label="platform_api", codename="change_projectamenity"))
    amenity = Amenity.objects.create(taxonomy_code="gym", display_name="Gym", category="health")
    claim = ProjectAmenity.objects.create(project=project, amenity=amenity,
        evidence_summary={"manual": [{"submittedBy": user.pk}], "reviewState": "unreviewed"})
    response = client.post(f"/api/v1/admin/projects/{project.pk}/review", {
        "kind": "amenity", "id": str(claim.pk), "decision": "accept",
        "expectedUpdatedAt": claim.updated_at.isoformat(),
        "availability_state": "available", "reason": "Verified"}, format="json")
    assert response.status_code == 403
    assert b"different administrator" in response.content
    claim.refresh_from_db()
    assert claim.evidence_summary["reviewState"] == "unreviewed"
