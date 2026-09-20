"""The only supported writes: staff model permission + validation + CAS + audit."""

from uuid import UUID, uuid4

from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone

from ..models import AuditEvent, FeatureFlag
from .registry import REGISTRY
from .validation import (
    MAX_VERSION, FlagValidationError, active_environment, validate_configuration, validate_version,
)

_UNSET = object()


class FeatureFlagConflict(ValueError):
    code = "VERSION_CONFLICT"

    def __init__(self):
        super().__init__("Feature flag changed; reload before retrying.")


class FeatureFlagUnavailable(ValueError):
    code = "FEATURE_FLAG_STORAGE_UNAVAILABLE"

    def __init__(self):
        super().__init__("Feature flag storage is unavailable.")


def can_manage(actor, action: str) -> bool:
    return bool(
        actor is not None and getattr(actor, "is_authenticated", False)
        and getattr(actor, "is_active", False) and getattr(actor, "is_staff", False)
        and actor.has_perm(f"platform_api.{action}_featureflag")
    )


def _authorize(actor, action: str):
    if not can_manage(actor, action):
        raise PermissionDenied("Feature flag model permission is required.")


def validate_mutation(*, key, environment, enabled, targeting_rules, instance=None):
    if environment != active_environment():
        raise FlagValidationError("inactive_environment")
    if instance is not None and (key != instance.key or environment != instance.environment):
        raise FlagValidationError("immutable_identity")
    return validate_configuration(key, enabled, targeting_rules)


def _audit(row, actor, action, previous_version, correlation_id=None):
    try:
        correlation = UUID(str(correlation_id)) if correlation_id else uuid4()
    except (ValueError, TypeError, AttributeError):
        correlation = uuid4()
    # Deliberately omit rule values, request bodies, user/project identifiers and
    # credentials. The actor FK is the standard audit attribution, not a decision.
    AuditEvent.objects.create(
        actor=actor, action=action, entity_type="FeatureFlag", entity_id=row.pk,
        correlation_id=correlation, reason_code="registry_sync" if actor is None else "staff_change",
        metadata={"key": row.key, "environment": row.environment, "enabled": row.enabled,
                  "previousVersion": previous_version, "version": row.version,
                  "targetingDimensions": sorted(row.targeting_rules)},
    )


def _insert(*, key, environment, enabled, targeting_rules, actor, correlation_id=None):
    """Internal insert, called only inside the shared transaction boundaries."""
    rules = validate_mutation(key=key, environment=environment, enabled=enabled,
                              targeting_rules=targeting_rules)
    row = FeatureFlag.objects.create(key=key, environment=environment, enabled=enabled,
                                     targeting_rules=rules, version=1)
    _audit(row, actor, "feature_flag.created", None, correlation_id)
    return row


def create_flag(*, key, actor, enabled=_UNSET, targeting_rules=_UNSET, environment=_UNSET, correlation_id=None):
    _authorize(actor, "add")
    definition = REGISTRY.get(key) if isinstance(key, str) else None
    if definition is None:
        raise FlagValidationError("unknown_key")
    try:
        with transaction.atomic():
            return _insert(
                key=key, environment=active_environment() if environment is _UNSET else environment,
                enabled=definition.default_enabled if enabled is _UNSET else enabled,
                targeting_rules={} if targeting_rules is _UNSET else targeting_rules,
                actor=actor, correlation_id=correlation_id,
            )
    except IntegrityError:
        raise FeatureFlagConflict() from None
    except DatabaseError:
        raise FeatureFlagUnavailable() from None


def update_flag(flag_id, *, actor, expected_version, changes, correlation_id=None):
    _authorize(actor, "change")
    validate_version(expected_version)
    if not isinstance(changes, dict) or set(changes) - {"key", "environment", "enabled", "targeting_rules"}:
        raise FlagValidationError("immutable_or_unknown_field")
    try:
        with transaction.atomic():
            row = FeatureFlag.objects.select_for_update().filter(
                pk=flag_id, environment=active_environment(),
            ).first()
            if row is None:
                raise FlagValidationError("flag_not_found")
            if row.key not in REGISTRY:
                raise FlagValidationError("unknown_key_read_only")
            validate_version(row.version)
            if row.version != expected_version:
                raise FeatureFlagConflict()
            if row.version == MAX_VERSION:
                raise FlagValidationError("version_exhausted")
            enabled = changes.get("enabled", row.enabled)
            rules = validate_mutation(
                key=changes.get("key", row.key), environment=changes.get("environment", row.environment),
                enabled=enabled, targeting_rules=changes.get("targeting_rules", row.targeting_rules),
                instance=row,
            )
            previous_version = row.version
            # The conditional UPDATE also protects backends where row locks are
            # not supported. A stale writer can never overwrite a newer version.
            row.enabled, row.targeting_rules = enabled, rules
            row.version, row.updated_at = previous_version + 1, timezone.now()
            changed = FeatureFlag.objects.filter(
                pk=row.pk, key=row.key, environment=row.environment, version=previous_version,
            ).update(enabled=row.enabled, targeting_rules=rules, version=row.version, updated_at=row.updated_at)
            if changed != 1:
                raise FeatureFlagConflict()
            _audit(row, actor, "feature_flag.updated" if enabled else "feature_flag.disabled",
                   previous_version, correlation_id)
            return row
    except DatabaseError:
        raise FeatureFlagUnavailable() from None


def disable_flag(flag_id, *, actor, expected_version, correlation_id=None):
    """Keep the row and its history. Deletion could resurrect an ON registry default."""
    return update_flag(flag_id, actor=actor, expected_version=expected_version,
                       changes={"enabled": False}, correlation_id=correlation_id)


def sync_defaults(*, apply=False) -> list[str]:
    """Explicit CLI/system entry point, no actor privilege bypass in the HTTP adapters.

    Return missing keys (or keys created with apply=True). Existing rows, including
    unknown keys and malformed configurations, are never rewritten.
    """
    if type(apply) is not bool:
        raise FlagValidationError("invalid_apply")
    environment = active_environment()
    try:
        with transaction.atomic():
            present = set(FeatureFlag.objects.filter(environment=environment).values_list("key", flat=True))
            missing = [key for key in REGISTRY if key not in present]
            if apply:
                for key in missing:
                    _insert(key=key, environment=environment, enabled=REGISTRY[key].default_enabled,
                            targeting_rules={}, actor=None)
            return missing
    except IntegrityError:
        raise FeatureFlagConflict() from None
    except DatabaseError:
        raise FeatureFlagUnavailable() from None