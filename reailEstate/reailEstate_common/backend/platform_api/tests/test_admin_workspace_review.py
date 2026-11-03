import hashlib
from io import BytesIO
from uuid import uuid4

import pytest
from django.contrib.auth.models import Permission
from django.utils import timezone

from platform_api.models import Amenity, AuditEvent, EvidenceDocument, ProjectAmenity
from .test_admin_project_workspace import workspace

pytestmark = pytest.mark.django_db


def grant(user, *names):
    user.user_permissions.add(*Permission.objects.filter(content_type__app_label="platform_api", codename__in=names))


def test_amenity_review_checks_permissions_versions_and_preserves_evidence(workspace):
    client, user, _, project = workspace
    item = ProjectAmenity.objects.create(project=project,
        amenity=Amenity.objects.create(taxonomy_code="pool", display_name="Pool", category="health"),
        evidence_summary={"source": "brochure", "reviewState": "unreviewed"})
    url = f"/api/v1/admin/projects/{project.pk}/review"
    body = {"kind": "amenity", "id": str(item.pk), "decision": "accept", "reason": "Checked source page",
            "expectedUpdatedAt": item.updated_at.isoformat()}
    assert client.post(url, body, format="json").status_code == 403
    grant(user, "view_project", "view_projectamenity", "change_projectamenity")
    response = client.post(url, body, format="json")
    assert response.status_code == 200, response.content
    item.refresh_from_db()
    assert item.evidence_summary["source"] == "brochure"
    assert item.evidence_summary["reviewState"] == "accepted"
    assert AuditEvent.objects.filter(action="project.amenity_reviewed", entity_id=item.pk).exists()
    assert client.post(url, body, format="json").status_code == 409
    assert client.get(url).json()["amenities"][0]["state"] == "accepted"


def test_amenity_review_blocks_self_approval_and_retired_projects(workspace):
    client, user, _, project = workspace
    grant(user, "change_projectamenity")
    item = ProjectAmenity.objects.create(project=project,
        amenity=Amenity.objects.create(taxonomy_code="gym", display_name="Gym", category="health"),
        evidence_summary={"manual": [{"submittedBy": user.pk}]})
    body = {"kind": "amenity", "id": str(item.pk), "decision": "accept", "reason": "Own claim",
            "expectedUpdatedAt": item.updated_at.isoformat()}
    url = f"/api/v1/admin/projects/{project.pk}/review"
    assert client.post(url, body, format="json").status_code == 403
    project.status = "retired"; project.save()
    assert client.post(url, {**body, "decision": "reject"}, format="json").status_code == 400


def test_original_document_requires_scope_clean_scan_and_checksum(workspace, monkeypatch):
    client, user, _, project = workspace
    content = b"private document"
    document = EvidenceDocument.objects.create(project=project, uploaded_by=user, original_name="evidence.txt",
        object_key=f"derived/{uuid4()}", declared_media_type="text/plain", byte_length=len(content),
        sha256=hashlib.sha256(content).hexdigest(), retention_until=timezone.now())
    url = f"/api/v1/admin/project-workspace/builder/projects/{project.pk}/documents/{document.pk}/original"
    assert client.get(url).status_code == 403
    document.scan_status = "clean"; document.save()
    from platform_api.artifact_storage import storage
    monkeypatch.setattr(storage, "open", lambda *args: BytesIO(content))
    response = client.get(url)
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == content
    assert response["Content-Disposition"].startswith("attachment;")
    assert response["Cache-Control"] == "private, no-store"
    assert client.get(url + "?metadata=1").json()["pageCount"] == 0
    monkeypatch.setattr(storage, "open", lambda *args: BytesIO(b"tampered"))
    assert client.get(url).status_code == 404
