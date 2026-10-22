"""Effective flags only; no registration, seeding or operation gates on import."""

from .evaluator import (
    Decision, FeatureContext, FeatureDecisions, FeatureDisabled, is_enabled, require_enabled, snapshot,
)
from .registry import REGISTRY, UI_FLAG_KEYS, FeatureDefinition

__all__ = [
    "Decision", "FeatureContext", "FeatureDecisions", "FeatureDefinition", "FeatureDisabled",
    "REGISTRY", "UI_FLAG_KEYS", "is_enabled", "require_enabled", "snapshot",
]