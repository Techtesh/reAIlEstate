"""Central flag infrastructure regressions. No operation gates or external calls."""

import io
import json
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from django.contrib.auth.models import AnonymousUser, Permission
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.db import DatabaseError, OperationalError

from platform_api.feature_flags import (
    REGISTRY, UI_FLAG_KEYS, FeatureContext, FeatureDecisions, FeatureDisabled,
    is_enabled, require_enabled, snapshot,
)
from platform_api.feature_flags.management import (
    FeatureFlagConflict, FeatureFlagUnavailable, create_flag, disable_flag, sync_defaults, update_flag,
)
from platform_api.feature_flags.validation import FlagValidationError, normalize_id, validate_configuration
from platform_api.models import AuditEvent, FeatureFlag, Organization, Project


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def flag_environment(settings):
    settings.ENVIRONMENT = "development"


@pytest.fixture
def flag_staff(django_user_model):
    actor = django_user_model.objects.create_user(username="flag-manager", is_staff=True)
    actor.user_permissions.add(*Permission.objects.filter(
        content_type__app_label="platform_api", codename__in=[
            "view_featureflag", "add_featureflag", "change_featureflag",
        ],
    ))
    return actor


def test_registry_is_small_frozen_and_compatibility_defaults_are_explicit():
    assert set(REGISTRY) == {
        "media.crop_drafts", "media.crop_attach", "media.crop_update", "media.layout_names",
        "media.layout_removal", "media.source_pages", "media.auto_segment", "media.extraction_queue",
        "operations.extraction_execute", "operations.legacy_destructive",
        "showDemoPinsOnBrowse", "recommendations.layout_hard_matches",
    }
    assert all(item.default_enabled for key, item in REGISTRY.items() if key not in {"operations.legacy_destructive", "recommendations.layout_hard_matches"})
    assert REGISTRY["operations.legacy_destructive"].default_enabled is False
    assert REGISTRY["operations.extraction_execute"].targetable is False
    with pytest.raises(FrozenInstanceError):
        setattr(REGISTRY["media.crop_drafts"], "default_enabled", False)
    with pytest.raises(TypeError):
        REGISTRY["new"] = REGISTRY["media.crop_drafts"]
    assert all(name.isidentifier() for name in UI_FLAG_KEYS)
    for definition in REGISTRY.values():
        assert set(definition.dependencies) <= set(REGISTRY)


def test_defaults_unknown_and_public_exception_contract(django_assert_num_queries):
    assert not FeatureFlag.objects.exists()
    with django_assert_num_queries(1):
        decisions = snapshot()
        assert decisions.is_enabled("media.crop_drafts")
        assert decisions.decision("media.crop_drafts").reason == "default_enabled"
        assert decisions.decision("media.crop_drafts").version is None
        assert not decisions.is_enabled("operations.legacy_destructive")
        unknown = decisions.decision("unknown-input-must-not-be-reflected")
        assert unknown.reason == "unknown_key" and unknown.key == ""
    assert not FeatureFlag.objects.exists()  # Evaluation never seeds defaults.
    assert is_enabled("media.crop_drafts")
    assert require_enabled("media.crop_drafts").enabled
    with pytest.raises(FeatureDisabled) as error:
        require_enabled("operations.legacy_destructive")
    assert isinstance(error.value, ValueError)
    assert error.value.code == "FEATURE_DISABLED"
    assert error.value.decision.reason == "default_disabled"


def test_snapshot_is_one_query_including_ui_and_dependencies(django_assert_num_queries):
    FeatureFlag.objects.create(key="media.crop_drafts", environment="development", enabled=False, version=7)
    with django_assert_num_queries(1):
        decisions = FeatureDecisions()
        for _ in range(3):
            all_decisions = decisions.snapshot()
            ui = decisions.ui_flags
            assert len(all_decisions) == len(REGISTRY)
            assert ui == {name: all_decisions[key].enabled for name, key in UI_FLAG_KEYS.items()}
            assert ui["media_crop_attach"] is False
    attach = decisions.decision("media.crop_attach")
    assert attach.reason == "dependency_disabled" and attach.dependency == "media.crop_drafts"
    assert dict(attach.versions) == {"media.crop_attach": None, "media.crop_drafts": 7}


def test_context_and_rules_use_consistent_ids_without_relation_queries(django_assert_num_queries):
    organization = Organization.objects.create(legal_name="Flag context")
    project = Project.objects.create(organization=organization, submitted_name="Context project")
    project = Project.objects.get(pk=project.pk)  # Organization relation deliberately not cached.
    with django_assert_num_queries(0):
        context = FeatureContext(actor=SimpleNamespace(pk=7, is_authenticated=True), project=project)
        assert context.user_id == "7"
        assert context.project_id == str(project.pk)
        assert context.organization_id == str(organization.pk)
    assert normalize_id(project.pk.hex.upper()) == str(project.pk)
    assert normalize_id("0007") == "7"
    assert normalize_id(UUID(str(project.pk))) == str(project.pk)
    assert FeatureContext(actor=AnonymousUser()).user_id is None
    # A deliberately deferred organization FK does not trigger a lazy DB read.
    deferred = Project.objects.only("id").get(pk=project.pk)
    with django_assert_num_queries(0):
        assert FeatureContext(project=deferred).organization_id is None


def test_targeting_is_deterministic_and_across_dimensions_or_within_values():
    project_id, organization_id = uuid4(), uuid4()
    project = SimpleNamespace(pk=project_id, organization_id=organization_id)
    actor = SimpleNamespace(pk=11, is_authenticated=True)
    rules = {"project_ids": [str(uuid4()), project_id.hex.upper()],
             "organization_ids": [str(organization_id)], "user_ids": [10, "0011"]}
    FeatureFlag.objects.create(key="media.layout_names", environment="development", enabled=True,
                               targeting_rules=rules, version=3)
    for _ in range(3):
        decision = FeatureDecisions(actor=actor, project=project).decision("media.layout_names")
        assert decision.enabled and decision.reason == "target_match" and decision.version == 3
        serialized = json.dumps(decision.as_dict())
        assert str(project_id) not in serialized and str(organization_id) not in serialized
        assert "targeting" not in serialized and "user_id" not in serialized
    assert not is_enabled("media.layout_names", actor=SimpleNamespace(pk=12), project=project)
    assert not is_enabled("media.layout_names", actor=actor)
    assert not is_enabled("media.layout_names", project=project)
    assert not is_enabled("media.layout_names", actor=AnonymousUser(), project=project)


@pytest.mark.parametrize("rules", [{"project_ids": []}, {"organization_ids": []}, {"user_ids": []}])
def test_empty_target_list_denies(rules):
    FeatureFlag.objects.create(key="media.crop_drafts", environment="development", enabled=True,
                               targeting_rules=rules)
    assert FeatureDecisions().decision("media.crop_drafts").reason == "target_mismatch"


@pytest.mark.parametrize("rules", [
    None, [], "credential-do-not-expose", {"percentage": 100}, {"user_ids": "1"},
    {"user_ids": [True]}, {"project_ids": [None]}, {"organization_ids": [{}]},
    {"project_ids": [" "]}, {"user_ids": [1.5]}, {"user_ids": ["secret\nvalue"]},
])
def test_malformed_rules_rejected_and_persisted_bad_rows_fail_closed(rules):
    with pytest.raises(FlagValidationError):
        validate_configuration("media.crop_drafts", True, rules)
    if rules is not None:  # Existing JSON field is NOT NULL.
        FeatureFlag.objects.create(key="media.crop_drafts", environment="development", enabled=True,
                                   targeting_rules=rules)
        decision = FeatureDecisions().decision("media.crop_drafts")
        assert not decision.enabled and decision.reason == "malformed_record"
        assert "credential" not in json.dumps(decision.as_dict())


@pytest.mark.parametrize("rules", [{"user_ids": []}, {"project_ids": ["1"]}, {"organization_ids": ["2"]}])
def test_emergency_execution_switch_cannot_be_targeted(rules):
    with pytest.raises(FlagValidationError, match="targeting_not_allowed"):
        validate_configuration("operations.extraction_execute", True, rules)
    FeatureFlag.objects.create(key="operations.extraction_execute", environment="development",
                               enabled=True, targeting_rules=rules)
    assert not is_enabled("operations.extraction_execute")


def test_execution_is_independent_of_enqueue_policy_and_requires_fresh_snapshot(flag_staff):
    cached = FeatureDecisions()
    create_flag(key="operations.extraction_execute", enabled=False, actor=flag_staff)
    assert cached.is_enabled("operations.extraction_execute")  # Request-scoped snapshot, not global cache.
    assert not is_enabled("operations.extraction_execute")
    assert is_enabled("media.extraction_queue")


def test_exact_environment_isolation_without_fallback(settings, flag_staff):
    FeatureFlag.objects.create(key="media.crop_drafts", environment="development", enabled=False)
    settings.ENVIRONMENT = "Production"
    FeatureFlag.objects.create(key="media.crop_drafts", environment="production", enabled=False)
    assert is_enabled("media.crop_drafts")  # Neither other environment is a fallback.
    created = create_flag(key="media.layout_names", actor=flag_staff)
    assert created.environment == "Production" and created.enabled
    with pytest.raises(FlagValidationError, match="inactive_environment"):
        create_flag(key="media.crop_update", actor=flag_staff, environment="production")


@pytest.mark.parametrize("environment", [None, "", " production", "production ", "*", 1])
def test_invalid_environment_denies_without_query(settings, environment, django_assert_num_queries):
    settings.ENVIRONMENT = environment
    with django_assert_num_queries(0):
        decisions = FeatureDecisions()
        assert all(not decision.enabled and decision.reason == "invalid_environment"
                   for decision in decisions.snapshot().values())


def test_db_error_and_invalid_context_fail_closed_without_leaking_details():
    with patch("platform_api.feature_flags.evaluator.FeatureFlag.objects.filter",
               side_effect=OperationalError("credential-do-not-expose")):
        decisions = FeatureDecisions()
    assert not any(decisions.ui_flags.values())
    assert decisions.decision("media.crop_drafts").reason == "storage_unavailable"
    assert "credential" not in json.dumps(decisions.decision("media.crop_drafts").as_dict())
    assert FeatureDecisions(actor=True).decision("media.crop_drafts").reason == "invalid_context"
    FeatureFlag.objects.create(key="media.crop_drafts", environment="development", enabled=True, version=0)
    assert FeatureDecisions().decision("media.crop_drafts").reason == "malformed_record"


def test_dependency_uses_same_target_context():
    FeatureFlag.objects.create(key="media.crop_drafts", environment="development", enabled=True,
                               targeting_rules={"user_ids": [5]}, version=4)
    assert not is_enabled("media.crop_attach", actor=6)
    assert is_enabled("media.crop_attach", actor=5)


def test_management_versions_audit_and_disable_preserve_row(flag_staff):
    row = create_flag(key="media.crop_drafts", actor=flag_staff, targeting_rules={"user_ids": [flag_staff.pk]})
    assert row.version == 1 and row.enabled
    changed = update_flag(row.pk, actor=flag_staff, expected_version=1, changes={"targeting_rules": {}})
    disabled = disable_flag(row.pk, actor=flag_staff, expected_version=2)
    assert changed.version == 2 and disabled.version == 3 and not disabled.enabled
    assert FeatureFlag.objects.filter(pk=row.pk).count() == 1
    events = list(AuditEvent.objects.filter(entity_id=row.pk).order_by("occurred_at"))
    assert [event.action for event in events] == ["feature_flag.created", "feature_flag.updated", "feature_flag.disabled"]
    assert [event.metadata["version"] for event in events] == [1, 2, 3]
    assert all(event.actor_id == flag_staff.pk and event.correlation_id for event in events)
    assert set(events[0].metadata) == {"key", "environment", "enabled", "previousVersion", "version", "targetingDimensions"}
    assert events[0].metadata["targetingDimensions"] == ["user_ids"]


def test_stale_and_immutable_mutations_do_not_save_or_audit(flag_staff):
    row = create_flag(key="media.crop_update", actor=flag_staff)
    disable_flag(row.pk, actor=flag_staff, expected_version=1)
    with pytest.raises(FeatureFlagConflict):
        update_flag(row.pk, actor=flag_staff, expected_version=1, changes={"enabled": True})
    for changes in ({"version": 99}, {"key": "media.crop_drafts"}, {"environment": "production"},
                    {"targeting_rules": {"percentage": 100}}, {"enabled": "true"}):
        with pytest.raises(FlagValidationError):
            update_flag(row.pk, actor=flag_staff, expected_version=2, changes=changes)
    for version in (None, "2", True, 0, -1):
        with pytest.raises(FlagValidationError):
            disable_flag(row.pk, actor=flag_staff, expected_version=version)
    row.refresh_from_db()
    assert row.version == 2 and not row.enabled
    assert AuditEvent.objects.filter(entity_id=row.pk).count() == 2


def test_compare_and_swap_guard_and_audit_failure_rollback(flag_staff):
    row = create_flag(key="media.layout_names", actor=flag_staff)
    with patch("django.db.models.query.QuerySet.update", return_value=0):
        with pytest.raises(FeatureFlagConflict):
            disable_flag(row.pk, actor=flag_staff, expected_version=1)
    with patch("platform_api.feature_flags.management.AuditEvent.objects.create",
               side_effect=DatabaseError("credential-do-not-expose")):
        with pytest.raises(FeatureFlagUnavailable) as error:
            disable_flag(row.pk, actor=flag_staff, expected_version=1)
        assert "credential" not in str(error.value)
        with pytest.raises(FeatureFlagUnavailable):
            create_flag(key="media.crop_attach", actor=flag_staff)
    row.refresh_from_db()
    assert row.version == 1 and row.enabled
    assert not FeatureFlag.objects.filter(key="media.crop_attach").exists()
    assert AuditEvent.objects.filter(entity_id=row.pk).count() == 1


@pytest.mark.parametrize("field", ["enabled", "targeting_rules", "environment"])
def test_shared_create_rejects_explicit_null(flag_staff, field):
    with pytest.raises(FlagValidationError):
        create_flag(key="media.crop_drafts", actor=flag_staff, **{field: None})
    assert not FeatureFlag.objects.exists() and not AuditEvent.objects.exists()


def test_unknown_rows_read_only_and_other_environment_untouched(flag_staff):
    unknown = FeatureFlag.objects.create(key="historical.flag", environment="development", enabled=True)
    other = FeatureFlag.objects.create(key="media.crop_drafts", environment="production", enabled=True)
    with pytest.raises(FlagValidationError):
        create_flag(key="historical.flag", actor=flag_staff)
    for row in (unknown, other):
        with pytest.raises(FlagValidationError):
            disable_flag(row.pk, actor=flag_staff, expected_version=1)
        row.refresh_from_db()
        assert row.enabled and row.version == 1
    assert not is_enabled("historical.flag")
    assert not AuditEvent.objects.exists()


@pytest.mark.parametrize("staff,active,grant_permission", [(False, True, True), (True, False, True), (True, True, False)])
def test_mutations_require_active_staff_model_permission(django_user_model, staff, active, grant_permission):
    actor = django_user_model.objects.create_user(username="limited", is_staff=staff, is_active=active)
    if grant_permission:
        actor.user_permissions.add(*Permission.objects.filter(content_type__app_label="platform_api",
                                                               codename__in=["add_featureflag", "change_featureflag"]))
    with pytest.raises(PermissionDenied):
        create_flag(key="media.crop_drafts", actor=actor)
    assert not FeatureFlag.objects.exists()


def test_seed_is_preview_first_idempotent_exact_environment_and_audited(settings):
    existing = FeatureFlag.objects.create(key="media.crop_drafts", environment="development", enabled=False,
                                         targeting_rules={"user_ids": []}, version=8)
    other = FeatureFlag.objects.create(key="media.crop_attach", environment="production", enabled=False, version=9)
    unknown = FeatureFlag.objects.create(key="historical.flag", environment="development", enabled=True)
    output = io.StringIO()
    call_command("sync_feature_flags", stdout=output)
    assert "Preview only" in output.getvalue()
    assert FeatureFlag.objects.count() == 3 and not AuditEvent.objects.exists()
    call_command("sync_feature_flags", apply=True, stdout=io.StringIO())
    assert FeatureFlag.objects.count() == len(REGISTRY) + 2
    assert AuditEvent.objects.count() == len(REGISTRY) - 1
    assert AuditEvent.objects.filter(actor__isnull=True, reason_code="registry_sync").count() == len(REGISTRY) - 1
    assert not FeatureFlag.objects.get(key="operations.legacy_destructive", environment="development").enabled
    existing.refresh_from_db()
    other.refresh_from_db()
    unknown.refresh_from_db()
    assert existing.version == 8 and not existing.enabled and existing.targeting_rules == {"user_ids": []}
    assert other.version == 9 and not other.enabled and unknown.enabled
    assert sync_defaults(apply=True) == []
    assert AuditEvent.objects.count() == len(REGISTRY) - 1
