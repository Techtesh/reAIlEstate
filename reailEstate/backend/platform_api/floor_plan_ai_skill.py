"""Explicit AI query skill for locating flats in one saved full-floor image."""

from .floor_plan_vision import (
    get_floor_plan_vision_provider,
    list_ai_floor_plan_vision_profiles,
    resolve_ai_vision_selection,
)


class FloorPlanFlatSeparationAISkill:
    """Invoke only a pinned, server-configured vision profile; never accept credentials."""

    name = "floor-plan-flat-separation-ai"

    @staticmethod
    def profiles():
        return list_ai_floor_plan_vision_profiles()

    @staticmethod
    def invoke(image_bytes, media_type, expected_count=None, vision_profile_id=None):
        selection = resolve_ai_vision_selection(vision_profile_id)
        provider = get_floor_plan_vision_provider(selection)
        regions, metadata = provider.analyze_unit_regions(image_bytes, media_type, expected_count)
        return regions, metadata | {"skill": FloorPlanFlatSeparationAISkill.name,
                                    "visionSelection": selection}