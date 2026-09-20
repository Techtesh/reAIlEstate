"""Rectangular room measurements; these are not certified carpet areas."""

import math


def room_area_sqft(length, width):
    if isinstance(length, bool) or isinstance(width, bool):
        return None
    try:
        length, width = float(length), float(width)
    except (TypeError, ValueError, OverflowError):
        return None
    area = length * width
    if not all(math.isfinite(value) and value > 0 for value in (length, width, area)):
        return None
    return round(area, 2)


def calculate_room_areas(dimensions):
    if not isinstance(dimensions, list):
        return []
    return [
        {**room, "areaSqFt": room_area_sqft(room.get("lengthFt"), room.get("widthFt"))}
        for room in dimensions if isinstance(room, dict)
    ]


def room_rectangle_sum_sqft(dimensions):
    areas = [room["areaSqFt"] for room in calculate_room_areas(dimensions) if room["areaSqFt"] is not None]
    total = sum(areas)
    return round(total, 2) if areas and math.isfinite(total) else None
