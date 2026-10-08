"""Small, immutable capability registry. Importing it never creates database rows."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class FeatureDefinition:
    key: str
    description: str
    default_enabled: bool
    dependencies: tuple[str, ...] = ()
    targetable: bool = True

    @property
    def ui_name(self) -> str:
        return self.key.replace(".", "_")


# Explicit compatibility defaults preserve existing tools when their registered
# row is missing. Unknown keys never inherit that compatibility behavior.
_DEFINITIONS = (
    FeatureDefinition("media.crop_drafts", "Create, list and discard private crop drafts.", True),
    FeatureDefinition("media.crop_attach", "Attach saved crop drafts to a project.", True,
                      dependencies=("media.crop_drafts",)),
    FeatureDefinition("media.crop_update", "Update the boundary of an existing unit crop.", True),
    FeatureDefinition("media.layout_names", "Edit reviewer-owned layout display names.", True),
    FeatureDefinition("media.layout_removal", "Reversibly remove selected project layouts.", True),
    FeatureDefinition("media.source_pages", "Select and add source document pages.", True),
    FeatureDefinition("media.auto_segment", "Request automatic separation of floor plans.", True,
                      dependencies=("media.extraction_queue",)),
    FeatureDefinition("media.extraction_queue", "Queue or retry layout extraction work.", True),
    FeatureDefinition("operations.extraction_execute", "Global emergency switch for extraction execution.",
                      True, targetable=False),
    FeatureDefinition("operations.legacy_destructive", "Opt in to legacy destructive operations.", False),
    FeatureDefinition("showDemoPinsOnBrowse", "Show unverified DemoPins projects in Browse.", True,
                      targetable=False),
    FeatureDefinition("recommendations.layout_hard_matches", "Enforce numeric hard requirements on the same validated layout.",
                      False, targetable=False),
)

REGISTRY: Mapping[str, FeatureDefinition] = MappingProxyType({item.key: item for item in _DEFINITIONS})
UI_FLAG_KEYS: Mapping[str, str] = MappingProxyType({item.ui_name: item.key for item in _DEFINITIONS})
