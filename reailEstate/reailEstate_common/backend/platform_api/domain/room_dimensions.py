"""Compatibility exports for common room measurements."""

from realestate_common.room_dimensions import (
    calculate_room_areas, room_area_sqft, room_rectangle_sum_sqft,
    normalize_room_measurements, text_room_measurements, dimension_warning,
)

__all__ = ["calculate_room_areas", "room_area_sqft", "room_rectangle_sum_sqft",
           "normalize_room_measurements", "text_room_measurements", "dimension_warning"]
