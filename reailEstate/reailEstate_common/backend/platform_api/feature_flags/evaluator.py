"""Request/job-scoped, deterministic effective decisions. Flags are not authorization."""

from dataclasses import dataclass, field
from typing import Any, Mapping

from django.db import DatabaseError

from ..models import FeatureFlag
from .registry import REGISTRY, UI_FLAG_KEYS
from .validation import (
    FlagValidationError, active_environment, normalize_id, validate_configuration, validate_version,
)


def _object_id(value: object) -> str | None:
    if value is None:
        return None
    return normalize_id(getattr(value, "pk", value))


@dataclass(frozen=True)
class FeatureContext:
    actor: Any = field(default=None, repr=False, compare=False)
    project: Any = field(default=None, repr=False, compare=False)
    user_id: str | None = field(default=None, init=False, repr=False)
    project_id: str | None = field(default=None, init=False, repr=False)
    organization_id: str | None = field(default=None, init=False, repr=False)
    valid: bool = field(default=True, init=False)

    def __post_init__(self):
        try:
            authenticated = getattr(self.actor, "is_authenticated", True)
            user_id = _object_id(self.actor) if authenticated else None
            project_id = _object_id(self.project)
            # Read the loaded FK ID, not project.organization (no relationship query).
            # Deferred organization_id is treated as absent, not lazily fetched.
            organization_id = getattr(self.project, "__dict__", {}).get("organization_id")
            object.__setattr__(self, "user_id", user_id)
            object.__setattr__(self, "project_id", project_id)
            object.__setattr__(self, "organization_id", _object_id(organization_id))
        except (FlagValidationError, AttributeError, TypeError, DatabaseError):
            object.__setattr__(self, "valid", False)


@dataclass(frozen=True)
class Decision:
    key: str
    enabled: bool
    reason: str
    environment: str | None
    source: str
    version: int | None = None
    versions: tuple[tuple[str, int | None], ...] = ()
    dependency: str | None = None

    def __bool__(self) -> bool:
        return self.enabled

    def as_dict(self) -> dict[str, object]:
        """Safe diagnostic payload: no actor/project IDs, targeting rules or DB errors."""
        return {
            "key": self.key, "enabled": self.enabled, "reason": self.reason,
            "environment": self.environment, "source": self.source, "version": self.version,
            "versions": dict(self.versions), "dependency": self.dependency,
        }


class FeatureDisabled(ValueError):
    code = "FEATURE_DISABLED"

    def __init__(self, decision: Decision):
        self.decision = decision
        super().__init__("Feature is disabled.")


class FeatureDecisions:
    """Load all registered keys in one query; reuse within one request/job only.

    Missing *registered* rows use explicit compatibility defaults. Malformed rows,
    invalid context/environment and database errors deny, never use defaults.
    No process-global cache: construct a new snapshot at an execution boundary.
    """

    def __init__(self, context: FeatureContext | None = None, *, actor=None, project=None):
        if context is not None and (actor is not None or project is not None):
            raise ValueError("Supply context or actor/project, not both.")
        self.context = context if context is not None else FeatureContext(actor=actor, project=project)
        self.environment: str | None = None
        self._error: str | None = None
        self._rows: dict[str, Mapping[str, Any]] = {}
        self._decisions: dict[str, Decision] = {}
        try:
            self.environment = active_environment()
            rows = list(FeatureFlag.objects.filter(
                environment=self.environment, key__in=tuple(REGISTRY),
            ).values("key", "enabled", "targeting_rules", "version"))
            for row in rows:
                if row["key"] in self._rows:
                    self._error = "malformed_record"
                self._rows[row["key"]] = row
        except FlagValidationError:
            self._error = "invalid_environment"
        except (DatabaseError, ValueError, TypeError):
            # Includes invalid JSON decoding; do not expose exception text.
            self._rows.clear()
            self._error = "storage_unavailable"

    def decision(self, key: str) -> Decision:
        return self._evaluate(key, frozenset())

    def _evaluate(self, key: str, visiting: frozenset[str]) -> Decision:
        if not isinstance(key, str) or key not in REGISTRY:
            # Do not reflect arbitrary unknown input into diagnostic payloads.
            return Decision("", False, "unknown_key", self.environment, "error")
        if key in self._decisions:
            return self._decisions[key]
        if self._error:
            return Decision(key, False, self._error, self.environment, "error")
        if not self.context.valid:
            return Decision(key, False, "invalid_context", self.environment, "error")
        if key in visiting:
            return Decision(key, False, "dependency_cycle", self.environment, "error")

        definition = REGISTRY[key]
        row = self._rows.get(key)
        source = "registry" if row is None else "database"
        version = None
        try:
            enabled = definition.default_enabled if row is None else row["enabled"]
            rules = validate_configuration(key, enabled, {} if row is None else row["targeting_rules"])
            if row is not None:
                version = validate_version(row["version"])
        except (FlagValidationError, KeyError, TypeError):
            result = Decision(key, False, "malformed_record", self.environment, "error")
            self._decisions[key] = result
            return result

        versions = {key: version}
        reason = "default_enabled" if row is None else "enabled"
        dependency = None
        if not enabled:
            reason = "default_disabled" if row is None else "disabled"
        else:
            identities = {
                "user_ids": self.context.user_id, "project_ids": self.context.project_id,
                "organization_ids": self.context.organization_id,
            }
            # AND across specified dimensions; OR within each list. [] always denies.
            if any(identities[dimension] not in values for dimension, values in rules.items()):
                enabled, reason = False, "target_mismatch"
            else:
                if rules:
                    reason = "target_match"
                for dependency_key in definition.dependencies:
                    effective = self._evaluate(dependency_key, visiting | {key})
                    versions.update(effective.versions)
                    if not effective.enabled:
                        enabled, reason, dependency = False, "dependency_disabled", dependency_key
                        break
        result = Decision(key, enabled, reason, self.environment, source, version,
                          tuple(sorted(versions.items())), dependency)
        self._decisions[key] = result
        return result

    def is_enabled(self, key: str) -> bool:
        return self.decision(key).enabled

    def require_enabled(self, key: str) -> Decision:
        result = self.decision(key)
        if not result.enabled:
            raise FeatureDisabled(result)
        return result

    def snapshot(self) -> dict[str, Decision]:
        return {key: self.decision(key) for key in REGISTRY}

    @property
    def ui_flags(self) -> dict[str, bool]:
        return {name: self.is_enabled(key) for name, key in UI_FLAG_KEYS.items()}


def is_enabled(key: str, actor=None, project=None) -> bool:
    return FeatureDecisions(actor=actor, project=project).is_enabled(key)


def require_enabled(key: str, actor=None, project=None) -> Decision:
    return FeatureDecisions(actor=actor, project=project).require_enabled(key)


def snapshot(actor=None, project=None) -> FeatureDecisions:
    return FeatureDecisions(actor=actor, project=project)