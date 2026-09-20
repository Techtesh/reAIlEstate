"""Shared strict schema validation, without imports from operation services."""

import re
from uuid import UUID

from django.conf import settings

from .registry import REGISTRY


class FlagValidationError(ValueError):
    """Only fixed codes are exposed; never include a supplied value in an error."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


DIMENSIONS = frozenset({"project_ids", "organization_ids", "user_ids"})
_ENVIRONMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
MAX_VERSION = 2_147_483_647


def active_environment() -> str:
    # No default, alias, lowercasing, wildcard or fallback to another environment.
    value = getattr(settings, "ENVIRONMENT", None)
    if not isinstance(value, str) or not _ENVIRONMENT.fullmatch(value):
        raise FlagValidationError("invalid_environment")
    return value


def normalize_id(value: object) -> str:
    """Canonical UUIDs and integer IDs; other opaque string IDs stay case-sensitive."""
    if isinstance(value, UUID):
        return str(value)
    if type(value) is int and value >= 0:
        return str(value)
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise FlagValidationError("invalid_identifier")
    try:
        return str(UUID(value))
    except ValueError:
        return str(int(value)) if value.isascii() and value.isdecimal() else value


def validate_version(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_VERSION:
        raise FlagValidationError("invalid_version")
    return value


def validate_configuration(key: str, enabled: object, rules: object) -> dict[str, list[str]]:
    definition = REGISTRY.get(key) if isinstance(key, str) else None
    if definition is None:
        raise FlagValidationError("unknown_key")
    if type(enabled) is not bool:
        raise FlagValidationError("invalid_enabled")
    if not isinstance(rules, dict) or any(dimension not in DIMENSIONS for dimension in rules):
        raise FlagValidationError("invalid_targeting_rules")
    if not definition.targetable and rules:
        raise FlagValidationError("targeting_not_allowed")
    normalized = {}
    for dimension, values in rules.items():
        if not isinstance(values, list):
            raise FlagValidationError("invalid_targeting_rules")
        normalized[dimension] = list(dict.fromkeys(normalize_id(value) for value in values))
    return normalized