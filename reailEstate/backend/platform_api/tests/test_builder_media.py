from uuid import uuid4
from unittest.mock import Mock

import pytest
from rest_framework.test import APIClient

from platform_api import floor_plan_vision
from platform_api.models import FloorPlan, LayoutCropDraft, Organization, OrganizationMembership, OutboxMessage, Project, ReviewTask
from .test_crop_from_page import page_source, REGION


pytestmark = pytest.mark.django_db


@pytest.fixture
def media(page_source, settings, monkeypatch):
    project, document, actor, *_ = page_source
    actor.is_staff = actor.is_superuser = False
    actor.save()
    OrganizationMembership.objects.create(user=actor, organization=project.organization, role="owner")
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.SECURE_SSL_REDIRECT = False
    settings.FLOOR_PLAN_VISION_PROFILES = {"test": {"provider": "ollama", "model": "offline-test"}}
    settings.FLOOR_PLAN_DEFAULT_VISION_PROFILE = "test"
    settings.FLOOR_PLAN_EXTRACTION_MODE = "ai"
    monkeypatch.setattr(floor_plan_vision, "get_floor_plan_vision_provider", Mock(side_effect=AssertionError("No inline AI")))
    client = APIClient()
    client.force_login(actor)
    return client, project, document, actor


def base(project):
    return f"/api/v1/builder/projects/{project.pk}/media"


def crop_body(document):
    return {"documentId": str(document.pk), "pageNumber": 2, "sourceSha256": document.sha256,
            "regions": [REGION], "requestId": str(uuid4())}


def stage(media):
    client, project, document, _ = media
    body = crop_body(document)
    response = client.post(base(project) + "/crops", body, format="json")
    assert response.status_code == 202, response.content
    return response.json()["draftIds"]


def attach(media, ids):
    client, project, _, _ = media
    body = {"action": "attach", "draftIds": ids, "confirm": True,
            "requestId": str(uuid4()), "visionProfileId": "test"}
    response = client.post(base(project) + "/drafts", body, format="json")
    assert response.status_code == 202, response.content
    return body, response.json()


def test_builder_media_crop_draft_preview_attach_and_replay(media):
    client, project, document, actor = media
    project.status = "published"
    project.save()
    result = client.get(base(project))
    assert result.status_code == 200, result.content
    assert result["Cache-Control"] == "private, no-store"
    assert result.json()["documents"][0]["pageCount"] == 3
    assert result.json()["projectPrice"] == "contact to know"
    preview = client.get(base(project) + f"/documents/{document.pk}/pages/2?sha256={document.sha256}")
    assert preview.status_code == 200 and preview["Content-Type"] == "image/png"
    body = crop_body(document)
    first = client.post(base(project) + "/crops", body, format="json")
    repeat = client.post(base(project) + "/crops", body, format="json")
    assert first.status_code == repeat.status_code == 202
    assert first.json() == repeat.json()
    assert LayoutCropDraft.objects.count() == 1 and not OutboxMessage.objects.exists()
    ids = first.json()["draftIds"]
    assert [draft["id"] for draft in client.get(base(project)).json()["drafts"]] == ids
    changed = dict(body, pageNumber=1)
    assert client.post(base(project) + "/crops", changed, format="json").status_code == 409
    image = client.get(base(project) + f"/drafts/{ids[0]}")
    assert image.status_code == 200 and image["Cache-Control"] == "private, no-store"
    commit, result = attach(media, ids)
    assert client.post(base(project) + "/drafts", commit, format="json").json() == result
    assert OutboxMessage.objects.count() == 1 and not LayoutCropDraft.objects.exists()
    plan = FloorPlan.objects.get(pk=result["planIds"][0])
    assert plan.review_state == "manual_crop_needs_evaluation"
    assert plan.project_id == project.pk and plan.document_id == document.pk
    project.refresh_from_db()
    assert project.status == "needs_review"
    assert ReviewTask.objects.filter(project=project, task_type="publication", state="open").exists()
    gallery = client.get(base(project)).json()
    assert any(item["planId"] == str(plan.pk) and item["category"] == "layouts" for item in gallery["items"])
    layout = next(item for item in gallery["items"] if item["planId"] == str(plan.pk))
    response = client.get(layout["imageUrl"])
    assert response.status_code == 200
    response.close()
    assert not actor.is_staff and not actor.is_superuser


@pytest.mark.parametrize("second_x", [.6, .15])
def test_multiple_layout_drafts_persist_and_appear_before_attach(media, second_x):
    client, project, document, actor = media
    body = crop_body(document)
    body["category"] = "layouts"
    body["regions"] = [dict(REGION, label="Layout A"), dict(REGION, x=second_x, label="Layout B")]
    response = client.post(base(project) + "/crops", body, format="json")
    assert response.status_code == 202, response.content
    ids = response.json()["draftIds"]
    assert len(ids) == 2
    assert LayoutCropDraft.objects.filter(owner=actor, project=project, pk__in=ids).count() == 2
    assert not OutboxMessage.objects.exists()
    listing = client.get(base(project)).json()["drafts"]
    assert {draft["id"] for draft in listing} == set(ids)
    assert {draft["label"] for draft in listing} == {"Layout A", "Layout B"}
    assert all(draft["category"] == "layouts" for draft in listing)
    for draft_id in ids:
        preview = client.get(base(project) + f"/drafts/{draft_id}")
        assert preview.status_code == 200
        assert preview.content.startswith(b"\x89PNG")
    assert client.post(base(project) + "/crops", body, format="json").json() == response.json()
    assert LayoutCropDraft.objects.count() == 2
    _, attached = attach(media, ids)
    assert len(attached["planIds"]) == OutboxMessage.objects.count() == 2
    assert not LayoutCropDraft.objects.exists()


@pytest.mark.parametrize("regions,code", [
    ([dict(REGION, width=.05, height=.05)], "INVALID_UNIT_REGIONS"),
    ([dict(REGION, x=0, y=0, width=.9, height=.9)], "INVALID_UNIT_REGIONS"),
])
def test_crop_rejections_do_not_create_partial_drafts(media, regions, code):
    client, project, document, _ = media
    body = dict(crop_body(document), regions=regions)
    response = client.post(base(project) + "/crops", body, format="json")
    assert response.status_code == 400
    assert response.json()["errors"]["detail"] == code
    assert not LayoutCropDraft.objects.exists()
    assert not OutboxMessage.objects.exists()


def test_automatic_region_validation_still_rejects_excessive_overlap():
    from platform_api.domain.geometry import validate_regions

    regions = [REGION, dict(REGION, x=.15, label="Adjacent home")]
    with pytest.raises(ValueError, match="OVERLAPPING_UNIT_REGIONS"):
        validate_regions(regions)
    assert len(validate_regions(regions, allow_overlap=True)) == 2


@pytest.mark.parametrize("width,expected", [(.199, 400), (.2, 202)])
def test_new_builder_layout_crops_require_two_percent_area(media, width, expected):
    client, project, document, _ = media
    body = dict(crop_body(document), category="layouts",
                regions=[REGION, dict(REGION, label="Boundary crop", width=width, height=.1)])
    response = client.post(base(project) + "/crops", body, format="json")
    assert response.status_code == expected, response.content
    if expected == 400:
        assert "at least 2%" in response.json()["errors"]["detail"]
        assert not LayoutCropDraft.objects.exists()
    else:
        assert LayoutCropDraft.objects.count() == 2
    assert not OutboxMessage.objects.exists()


def test_small_nonlayout_crops_keep_the_existing_area_rule(media):
    client, project, document, _ = media
    body = dict(crop_body(document), category="amenities",
                regions=[dict(REGION, width=.15, height=.1)])
    response = client.post(base(project) + "/crops", body, format="json")
    assert response.status_code == 202, response.content
    assert LayoutCropDraft.objects.count() == 1


def test_media_shares_admin_room_areas_and_current_evaluation_states(media):
    from django.utils import timezone
    from platform_api.layout_evaluation_status import layout_evaluation_states
    from platform_api.models import Operation

    client, project, _, _ = media
    _, attached = attach(media, stage(media))
    plan = FloorPlan.objects.get(pk=attached["planIds"][0])
    message = OutboxMessage.objects.get()
    operation = Operation.objects.get(pk=message.payload["operationId"])

    def current():
        data = client.get(base(project)).json()
        item = next(item for item in data["items"] if item["planId"] == str(plan.pk))
        assert data["jobs"][0]["planId"] == str(plan.pk)
        assert OutboxMessage.objects.count() == 1
        return item

    assert current()["evaluation"]["state"] == "queued"
    assert current()["dimensions"] == []
    plan.dimensions = [{"room": "Living room", "lengthFt": 12.5, "widthFt": 10, "areaSqFt": 999},
                       {"room": "Kitchen", "lengthFt": 8}]
    plan.room_count = 2
    plan.bathroom_count = 1
    plan.balcony_count = 0
    plan.orientation = "East"
    plan.features = {"balcony": False}
    plan.save()
    operation.state = "completed"
    operation.save()
    message.published_at = timezone.now()
    message.save()
    item = current()
    assert item["evaluation"] == layout_evaluation_states([plan])[str(plan.pk)]
    assert item["dimensions"][0]["areaSqFt"] == 125
    assert item["dimensions"][1]["areaSqFt"] is None
    assert item["roomRectangleSumSqFt"] == 125
    assert (item["roomCount"], item["bathrooms"], item["balconies"]) == (2, 1, 0)
    assert item["orientation"] == "East" and item["features"] == {"balcony": False}
    assert "rawExtraction" not in item
    plan.dimensions = []
    plan.save()
    assert "no readable room dimensions" in current()["evaluation"]["label"]
    operation.state, operation.error_code = "failed", "MANUAL_CROP_NOT_SINGLE_LAYOUT"
    operation.save()
    assert current()["evaluation"]["state"] == "failed"
    assert current()["evaluation"]["error"] == "MANUAL_CROP_NOT_SINGLE_LAYOUT"


def test_name_confirmation_defaults_off_and_is_scoped_to_the_organization(media):
    from django.contrib import admin

    client, project, _, actor = media
    assert project.organization.confirm_name_changes is False
    assert client.get(base(project)).json()["confirmNameChanges"] is False
    organization = project.organization
    organization.confirm_name_changes = True
    organization.save(update_fields=["confirm_name_changes", "updated_at"])
    assert client.get(base(project)).json()["confirmNameChanges"] is True
    other = Organization.objects.create(legal_name="Separate settings")
    OrganizationMembership.objects.create(user=actor, organization=other, role="owner")
    other_project = Project.objects.create(organization=other, submitted_name="Separate project")
    assert client.get(base(other_project)).json()["confirmNameChanges"] is False
    assert "confirm_name_changes" in admin.site._registry[Organization].list_display
    assert not OutboxMessage.objects.exists()


@pytest.mark.parametrize("role", ["viewer", "inactive", "inactive_org", "foreign", "staff_without_membership"])
def test_media_scope_applies_to_reads_previews_and_mutations(media, role):
    client, project, document, actor = media
    ids = stage(media)
    membership = OrganizationMembership.objects.get(user=actor)
    if role == "viewer":
        membership.role = "viewer"
        membership.save()
    elif role == "inactive":
        membership.status = "inactive"
        membership.save()
    elif role == "foreign":
        membership.organization = Organization.objects.create(legal_name="Foreign")
        membership.save()
    elif role == "inactive_org":
        project.organization.status = "inactive"
        project.organization.save()
    else:
        membership.delete()
        actor.is_staff = actor.is_superuser = True
        actor.save()
    for url in [base(project), base(project) + f"/drafts/{ids[0]}",
                base(project) + f"/documents/{document.pk}/pages/1?sha256={document.sha256}"]:
        assert client.get(url).status_code == 404
    assert client.post(base(project) + "/crops", crop_body(document), format="json").status_code == 404
    assert not OutboxMessage.objects.exists()


def test_cross_project_document_and_layout_and_other_users_draft_are_rejected(media, django_user_model):
    client, project, document, actor = media
    other = Project.objects.create(organization=project.organization, submitted_name="Another project")
    assert client.post(base(other) + "/crops", crop_body(document), format="json").status_code == 404
    ids = stage(media)
    peer = django_user_model.objects.create_user(username="peer")
    OrganizationMembership.objects.create(user=peer, organization=project.organization, role="editor")
    client.force_login(peer)
    assert client.get(base(project) + f"/drafts/{ids[0]}").status_code == 404
    response = client.post(base(project) + "/drafts", {"action": "attach", "draftIds": ids, "confirm": True,
        "requestId": str(uuid4())}, format="json")
    assert response.status_code == 400 and not OutboxMessage.objects.exists()
    client.force_login(actor)
    _, result = attach(media, ids)
    plan = FloorPlan.objects.get(pk=result["planIds"][0])
    response = client.post(base(other) + "/evaluate", {"planId": str(plan.pk),
        "expectedUpdatedAt": plan.updated_at.isoformat(), "confirm": True, "requestId": str(uuid4())}, format="json")
    assert response.status_code == 404
    assert client.get(base(other) + f"/assets/{plan.asset_id}").status_code == 404


def test_crop_rejects_stale_source_and_archived_project(media):
    client, project, document, _ = media
    values = crop_body(document)
    values["sourceSha256"] = "0" * 64
    assert client.post(base(project) + "/crops", values, format="json").status_code == 409
    project.status = "retired"
    project.save()
    assert client.post(base(project) + "/crops", crop_body(document), format="json").status_code == 409
    assert not LayoutCropDraft.objects.exists()
    assert client.get(base(project)).json()["canEdit"] is False


def test_re_evaluation_is_confirmed_versioned_and_replay_safe(media):
    client, project, _, _ = media
    _, result = attach(media, stage(media))
    plan = FloorPlan.objects.get(pk=result["planIds"][0])
    message = OutboxMessage.objects.get()
    from django.utils import timezone
    from platform_api.models import Operation
    message.published_at = timezone.now()
    message.save()
    Operation.objects.filter(pk=message.payload["operationId"]).update(state="completed")
    plan.review_state = "approved"
    plan.save()
    project.status = "published"
    project.save()
    body = {"planId": str(plan.pk), "expectedUpdatedAt": plan.updated_at.isoformat(),
            "confirm": False, "requestId": str(uuid4()), "visionProfileId": "test"}
    assert client.post(base(project) + "/evaluate", body, format="json").status_code == 400
    body["confirm"] = True
    stale = dict(body, expectedUpdatedAt="2000-01-01T00:00:00Z")
    assert client.post(base(project) + "/evaluate", stale, format="json").status_code == 409
    response = client.post(base(project) + "/evaluate", body, format="json")
    assert response.status_code == 202, response.content
    assert client.post(base(project) + "/evaluate", body, format="json").json() == response.json()
    assert OutboxMessage.objects.count() == 2
    plan.refresh_from_db()
    project.refresh_from_db()
    assert plan.review_state == "needs_review" and project.status == "needs_review"


@pytest.mark.parametrize("category", ["floor-plans", "amenities"])
def test_source_page_addition_uses_existing_queued_pipeline(media, category):
    client, project, document, _ = media
    body = {"documentId": str(document.pk), "sourceSha256": document.sha256, "pages": [1],
            "category": category, "confirm": True, "requestId": str(uuid4()), "visionProfileId": "test"}
    response = client.post(base(project) + "/pages", body, format="json")
    assert response.status_code == 202, response.content
    assert client.post(base(project) + "/pages", body, format="json").json() == response.json()
    assert OutboxMessage.objects.count() == 1


def test_processing_requires_session_csrf(media):
    _, project, document, actor = media
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(actor)
    assert client.post(base(project) + "/crops", crop_body(document), format="json").status_code == 403


def test_media_parity_smoke(media):
    client, project, document, actor = media
    _, created = attach(media, stage(media))
    plan = FloorPlan.objects.get(pk=created["planIds"][0])
    for action, extra in [("rename", {"name": "Builder alias"}), ("set-active", {"active": False}),
                          ("set-active", {"active": True}), ("remove", {}), ("restore", {})]:
        plan.refresh_from_db()
        response = client.post(base(project) + "/actions", {"action": action, "confirm": True,
            "requestId": str(uuid4()), "targets": [{"planId": str(plan.pk), "expectedUpdatedAt": plan.updated_at.isoformat()}],
            **extra}, format="json")
        assert response.status_code == 202, (action, response.content)
    plan.refresh_from_db()
    assert plan.display_name == "Builder alias"
    values = dict(crop_body(document), targetPlanId=str(plan.pk),
                  expectedUpdatedAt=plan.updated_at.isoformat(), category="layouts")
    updated = client.post(base(project) + "/crops", values, format="json")
    assert updated.status_code == 202, updated.content
    attach(media, updated.json()["draftIds"])
    plan.refresh_from_db()
    assert plan.display_name == "Builder alias"
    amenity = client.post(base(project) + "/crops", dict(crop_body(document), category="amenities"), format="json")
    assert amenity.status_code == 202, amenity.content
    attach(media, amenity.json()["draftIds"])
    assert project.floor_plan_assets.filter(source_bounds__category="amenities").exists()
    project.refresh_from_db()
    picture = client.post(base(project) + "/picture", {"source": "asset", "assetId": str(plan.asset_id),
        "expectedUpdatedAt": project.updated_at.isoformat(), "requestId": str(uuid4())}, format="json")
    assert picture.status_code == 202, picture.content
    for action in ["normalize", "reset-crop"]:
        plan.refresh_from_db()
        response = client.post(base(project) + "/actions", {"action": action, "confirm": True,
            "requestId": str(uuid4()), "targets": [{"planId": str(plan.pk), "expectedUpdatedAt": plan.updated_at.isoformat()}],
            **({"rotationDegrees": 90} if action == "normalize" else {})}, format="json")
        assert response.status_code == 202, (action, response.content)
    project.refresh_from_db()
    assert project.status != "published" and not actor.is_staff


def test_media_discard_and_unknown_fields(media):
    client, project, document, _ = media
    values = dict(crop_body(document), reviewState="approved")
    assert client.post(base(project) + "/crops", values, format="json").status_code == 400
    ids = stage(media)
    body = {"action": "discard", "draftIds": ids, "confirm": True, "requestId": str(uuid4())}
    assert client.post(base(project) + "/drafts", body, format="json").status_code == 202
    assert not LayoutCropDraft.objects.exists() and not OutboxMessage.objects.exists()


def test_disabled_crop_flag_and_failed_action_do_not_leave_partial_changes(media, monkeypatch):
    from platform_api import builder_media, crop_from_page
    client, project, document, _ = media
    monkeypatch.setattr(crop_from_page, "require_enabled", Mock(side_effect=ValueError("Crop drafts disabled")))
    response = client.post(base(project) + "/crops", crop_body(document), format="json")
    assert response.status_code == 400 and not LayoutCropDraft.objects.exists()
    monkeypatch.undo()
    ids = stage(media)
    monkeypatch.setattr(builder_media, "require_new_review", Mock(side_effect=ValueError("Review temporarily unavailable")))
    body = {"action": "attach", "draftIds": ids, "confirm": True, "requestId": str(uuid4()), "visionProfileId": "test"}
    assert client.post(base(project) + "/drafts", body, format="json").status_code == 400
    assert LayoutCropDraft.objects.count() == 1
    assert not OutboxMessage.objects.exists() and not FloorPlan.objects.exists()
