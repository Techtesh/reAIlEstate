from copy import deepcopy

import pytest

from platform_api.domain.room_dimensions import calculate_room_areas, room_area_sqft, room_rectangle_sum_sqft


@pytest.mark.parametrize(("length", "width", "expected"), [
    (12, 10, 120), (1.5, 3.92, 5.88), ("3.08", "4.42", 13.61),
    (None, 10, None), (10, None, None), (True, 10, None),
    (-10, -10, None), (0, 10, None), ("unknown", 10, None),
    (float("nan"), 10, None), (float("inf"), 10, None), (1e308, 10, None),
])
def test_room_area_uses_finite_positive_length_and_width(length, width, expected):
    assert room_area_sqft(length, width) == expected


def test_room_areas_replace_stale_calculations_without_mutating_evidence():
    rooms = [
        {"room": "Living", "lengthFt": 12, "widthFt": 10, "areaSqFt": 999, "evidence": "label"},
        {"room": "Partial", "lengthFt": 10, "widthFt": None, "areaSqFt": 100},
        {"room": "Nook", "lengthFt": 1.5, "widthFt": 3.92},
    ]
    original = deepcopy(rooms)
    calculated = calculate_room_areas(rooms)
    assert [row["areaSqFt"] for row in calculated] == [120, None, 5.88]
    assert calculated[0]["evidence"] == "label"
    assert room_rectangle_sum_sqft(rooms) == 125.88
    assert rooms == original


@pytest.mark.parametrize("rooms", [None, {}, [], [None], [{"lengthFt": 10}]])
def test_missing_measurements_have_no_calculated_total(rooms):
    assert room_rectangle_sum_sqft(rooms) is None
