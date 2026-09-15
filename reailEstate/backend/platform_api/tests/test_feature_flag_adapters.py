"""Adapter tests use an isolated admin site; the protected main admin is not changed."""

import json

import pytest
from django.contrib.admin import AdminSite
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from rest_framework.test import APIRequestFactory, force_authenticate

from platform_api.feature_flags.admin import FeatureFlagAdmin
from platform_api.feature_flags.api import FeatureFlagAdminViewSet, parse_if_match
from platform_api.feature_flags.management import FeatureFlagConflict, create_flag, disable_flag
from platform_api.feature_flags.serializers import FeatureFlagSerializer
from platform_api.models import AuditEvent, FeatureFlag

from .test_feature_flags import flag_environment, flag_staff  # noqa: F401 — shared fixtures


pytestmark = pytest.mark.django_db


def api_request(actor, method, action, *, row=None, data=None, version=None):
    factory = APIRequestFactory()
    headers = {} if version is None else {"HTTP_IF_MATCH": version}
    request = getattr(factory, method)("/api/v1/admin/feature-flags/", data=data, format="json", **headers)
    force_authenticate(request, user=actor)
    return FeatureFlagAdminViewSet.as_view({method: action})(request, **({"pk": row.pk} if row else {}))


def test_compatibility_exports_remain_available():
    from platform_api.serializers import FeatureFlagSerializer as ExportedSerializer
    from platform_api.views import FeatureFlagAdminViewSet as ExportedViewSet

    assert ExportedSerializer is FeatureFlagSerializer
    assert ExportedViewSet is FeatureFlagAdminViewSet


def test_api_create_patch_if_match_disable_and_delete_contract(flag_staff):
    response = api_request(flag_staff, "post", "create", data={"key": "media.crop_drafts"})
    assert response.status_code == 201
    assert response.data["environment"] == "development" and response.data["enabled"]
    assert response.data["version"] == 1 and response["ETag"] == 'W/"1"'
    row = FeatureFlag.objects.get(pk=response.data["id"])
    missing = api_request(flag_staff, "patch", "partial_update", row=row, data={"enabled": False})
    assert missing.status_code == 428
    updated = api_request(flag_staff, "patch", "partial_update", row=row, data={"enabled": False}, version='W/"1"')
    assert updated.status_code == 200 and updated["ETag"] == 'W/"2"'
    assert updated["Cache-Control"] == "no-store"
    stale = api_request(flag_staff, "patch", "partial_update", row=row, data={"enabled": True}, version='"1"')
    assert stale.status_code == 409
    forged = api_request(flag_staff, "patch", "partial_update", row=row, data={"version": 90}, version='"2"')
    assert forged.status_code == 400
    disabled = api_request(flag_staff, "post", "disable", row=row, version='"2"')
    assert disabled.status_code == 200 and not disabled.data["enabled"] and disabled.data["version"] == 3
    deleted = api_request(flag_staff, "delete", "destroy", row=row)
    assert deleted.status_code == 405
    assert FeatureFlag.objects.filter(pk=row.pk).exists()
    assert AuditEvent.objects.filter(entity_id=row.pk).count() == 3


@pytest.mark.parametrize("header", ["*", 'W/1', '"1', '1"', '"1","2"', '"0"', '"-1"', '"true"', '"999999999999999999"'])
def test_if_match_is_strict(header):
    from rest_framework.exceptions import APIException

    with pytest.raises(APIException):
        parse_if_match(header)


@pytest.mark.parametrize("header", ['W/"4"', '"4"', '4'])
def test_if_match_accepts_compatible_numeric_versions(header):
    assert parse_if_match(header) == 4


def test_api_permissions_require_more_than_staff(django_user_model):
    staff = django_user_model.objects.create_user(username="staff-no-flags", is_staff=True)
    assert api_request(staff, "get", "list").status_code == 403
    assert api_request(staff, "post", "create", data={"key": "media.crop_drafts"}).status_code == 403
    assert not FeatureFlag.objects.exists()


def test_api_view_only_permission_cannot_mutate(django_user_model):
    viewer = django_user_model.objects.create_user(username="flag-viewer", is_staff=True)
    viewer.user_permissions.add(Permission.objects.get(content_type__app_label="platform_api", codename="view_featureflag"))
    row = FeatureFlag.objects.create(key="media.crop_drafts", environment="development", enabled=True)
    assert api_request(viewer, "get", "retrieve", row=row).status_code == 200
    assert api_request(viewer, "patch", "partial_update", row=row, data={"enabled": False}, version='"1"').status_code == 403
    assert api_request(viewer, "post", "disable", row=row, version='"1"').status_code == 403
    assert api_request(viewer, "post", "create", data={"key": "media.crop_update"}).status_code == 403
    assert not AuditEvent.objects.exists()


def test_api_environment_isolation_and_unknown_rows_read_only(flag_staff):
    other = FeatureFlag.objects.create(key="media.crop_drafts", environment="production", enabled=False)
    old = FeatureFlag.objects.create(key="historical.flag", environment="development", enabled=True,
                                    targeting_rules={"credential": "must-not-be-exposed"})
    view = FeatureFlagAdminViewSet()
    assert set(view.get_queryset().values_list("pk", flat=True)) == {old.pk}
    listing = api_request(flag_staff, "get", "list")
    assert listing.status_code == 200
    assert [item["key"] for item in listing.data["results"]] == [old.key]
    assert api_request(flag_staff, "get", "retrieve", row=other).status_code == 404
    assert api_request(flag_staff, "patch", "partial_update", row=other, data={"enabled": True}, version='"1"').status_code == 404
    response = api_request(flag_staff, "get", "retrieve", row=old)
    assert response.status_code == 200 and not response.data["registered"] and not response.data["editable"]
    assert response.data["targeting_rules"] == {}
    assert "credential" not in json.dumps(response.data, default=str)
    assert api_request(flag_staff, "post", "disable", row=old, version='"1"').status_code == 400
    invalid = api_request(flag_staff, "post", "create", data={"key": "media.crop_update", "environment": "production"})
    assert invalid.status_code == 400


@pytest.mark.parametrize("data", [
    {"version": 99}, {"enabled": "true"}, {"targeting_rules": {"percentage": 100}},
    {"targeting_rules": None}, {"environment": " development "}, {"key": "not.registered"},
])
def test_api_rejects_invalid_configuration(flag_staff, data):
    response = api_request(flag_staff, "post", "create", data={"key": "media.crop_update"} | data)
    assert response.status_code == 400
    assert not FeatureFlag.objects.exists() and not AuditEvent.objects.exists()


def test_camelcase_wire_rules_are_normalized_and_duplicates_rejected(flag_staff):
    response = api_request(flag_staff, "post", "create", data={
        "key": "media.crop_update", "targetingRules": {"userIds": [flag_staff.pk]},
    })
    assert response.status_code == 201
    row = FeatureFlag.objects.get(pk=response.data["id"])
    assert row.targeting_rules == {"user_ids": [str(flag_staff.pk)]}
    response = api_request(flag_staff, "patch", "partial_update", row=row, version='"1"', data={
        "targetingRules": {"userIds": [1], "user_ids": [2]},
    })
    assert response.status_code == 400


def test_serializer_alone_cannot_bypass_versions_or_shared_permissions(flag_staff):
    row = create_flag(key="media.crop_update", actor=flag_staff)
    serializer = FeatureFlagSerializer(row, data={"enabled": False}, partial=True)
    assert not serializer.is_valid()
    serializer = FeatureFlagSerializer(row, data={"enabled": False}, partial=True, context={"expected_version": 1})
    assert serializer.is_valid(), serializer.errors
    with pytest.raises(PermissionDenied):
        serializer.save()


def admin_request(actor, data=None):
    request = RequestFactory().get("/admin/") if data is None else RequestFactory().post("/admin/", data)
    request.user = actor
    return request


def test_model_admin_fields_form_and_audited_save(flag_staff):
    adapter = FeatureFlagAdmin(FeatureFlag, AdminSite(name="flag-test"))
    row = create_flag(key="media.crop_drafts", actor=flag_staff)
    request = admin_request(flag_staff)
    assert set(adapter.get_readonly_fields(request, row)) >= {"key", "environment", "version"}
    form_class = adapter.get_form(request, row)
    form = form_class(instance=row)
    assert form.fields["expected_version"].widget.is_hidden
    assert form.initial["expected_version"] == 1
    assert "version" not in form.fields and "key" not in form.fields and "environment" not in form.fields
    form = form_class(data={"expected_version": 1, "targeting_rules": "{}"}, instance=row)
    assert form.is_valid(), form.errors
    obj = form.save(commit=False)
    adapter.save_model(request, obj, form, True)
    row.refresh_from_db()
    assert not row.enabled and row.version == 2
    assert AuditEvent.objects.filter(entity_id=row.pk, action="feature_flag.disabled").count() == 1


def test_model_admin_create_uses_current_env_and_shared_mutation(flag_staff, settings):
    settings.ENVIRONMENT = "Production"
    adapter = FeatureFlagAdmin(FeatureFlag, AdminSite(name="flag-create"))
    request = admin_request(flag_staff)
    form_class = adapter.get_form(request)
    assert form_class().initial["environment"] == "Production"
    form = form_class(data={"key": "media.crop_drafts", "environment": "Production",
                            "enabled": "on", "targeting_rules": "{}"})
    assert form.is_valid(), form.errors
    obj = form.save(commit=False)
    adapter.save_model(request, obj, form, False)
    assert obj.version == 1 and obj.environment == "Production"
    assert FeatureFlag.objects.filter(pk=obj.pk).exists()
    assert AuditEvent.objects.get(entity_id=obj.pk).action == "feature_flag.created"


@pytest.mark.parametrize("extra", [{"version": 99}, {"key": "media.layout_names"}, {"environment": "production"},
                                   {"expected_version": ""}, {"targeting_rules": '{"percentage": 100}'}])
def test_model_admin_rejects_forged_and_malformed_fields(flag_staff, extra):
    adapter = FeatureFlagAdmin(FeatureFlag, AdminSite(name="flag-forged"))
    row = create_flag(key="media.crop_drafts", actor=flag_staff)
    form_class = adapter.get_form(admin_request(flag_staff), row)
    form = form_class(data={"expected_version": 1, "enabled": "on", "targeting_rules": "{}"} | extra, instance=row)
    assert not form.is_valid()
    row.refresh_from_db()
    assert row.version == 1 and row.enabled


def test_model_admin_stale_form_and_race_cannot_overwrite(flag_staff):
    adapter = FeatureFlagAdmin(FeatureFlag, AdminSite(name="flag-race"))
    row = create_flag(key="media.crop_drafts", actor=flag_staff)
    request = admin_request(flag_staff)
    form_class = adapter.get_form(request, row)
    form = form_class(data={"expected_version": 1, "enabled": "on", "targeting_rules": "{}"}, instance=row)
    assert form.is_valid(), form.errors
    obj = form.save(commit=False)
    disable_flag(row.pk, actor=flag_staff, expected_version=1)
    with pytest.raises(FeatureFlagConflict):
        adapter.save_model(request, obj, form, True)
    row.refresh_from_db()
    assert row.version == 2 and not row.enabled
    stale = form_class(data={"expected_version": 1, "enabled": "on", "targeting_rules": "{}"}, instance=row)
    assert not stale.is_valid()
    assert AuditEvent.objects.filter(entity_id=row.pk).count() == 2


def test_model_admin_unknown_and_inactive_rows_read_only_delete_denied(flag_staff):
    adapter = FeatureFlagAdmin(FeatureFlag, AdminSite(name="flag-readonly"))
    request = admin_request(flag_staff)
    unknown = FeatureFlag.objects.create(key="historical.flag", environment="development", enabled=True)
    inactive = FeatureFlag.objects.create(key="media.crop_drafts", environment="production", enabled=True)
    for row in (unknown, inactive):
        assert adapter.has_view_permission(request, row)
        assert not adapter.has_change_permission(request, row)
        assert not adapter.has_delete_permission(request, row)
        assert "expected_version" not in adapter.get_fields(request, row)
        assert {"key", "environment", "enabled", "targeting_rules"} <= set(adapter.get_readonly_fields(request, row))
        with pytest.raises(PermissionDenied):
            adapter.delete_model(request, row)
    with pytest.raises(PermissionDenied):
        adapter.delete_queryset(request, FeatureFlag.objects.all())
    assert not adapter.get_actions(request)
    assert FeatureFlag.objects.count() == 2