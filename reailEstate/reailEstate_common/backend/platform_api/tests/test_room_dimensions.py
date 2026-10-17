from copy import deepcopy

import pytest

from platform_api.domain.room_dimensions import (
    calculate_room_areas, room_area_sqft, room_rectangle_sum_sqft, normalize_room_measurements, text_room_measurements,
)


def native_room(room, length, width, unit="ft", **flags):
    return {"room": room, "measurements": [{"length": str(length) if length is not None else None,
        "width": str(width) if width is not None else None, "unit": unit, "unitSource": "printed",
        "unitEvidence": f"{length} x {width} {unit}", "lengthEstimated": False, "widthEstimated": False, **flags}]}


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


@pytest.mark.parametrize("reverse", [False, True])
def test_dual_units_prefer_printed_feet_regardless_of_order(reverse):
    room = native_room("Living", "7' 9\"", "3' 11\"")
    room["measurements"] += native_room("Living", 2.35, 1.20, "m")["measurements"]
    if reverse:
        room["measurements"].reverse()
    before = deepcopy(room)
    dimensions, metadata = normalize_room_measurements([room])
    assert dimensions[0]["lengthFt"] == 7.75
    assert dimensions[0]["widthFt"] == pytest.approx(3 + 11 / 12)
    assert metadata["dimensionWarning"] == "" and room == before
    selected = metadata["dimensionEvidence"][0]["selectedIndex"]
    assert room["measurements"][selected]["unit"] == "ft"


def test_metric_conversion_and_area_use_unrounded_values():
    dimensions, metadata = normalize_room_measurements([native_room("Living", 7, 3.87, "m")])
    assert dimensions[0]["lengthFt"] == pytest.approx(7 / 0.3048)
    assert dimensions[0]["widthFt"] == pytest.approx(3.87 / 0.3048)
    assert calculate_room_areas(dimensions)[0]["areaSqFt"] == round(7 * 3.87 / 0.3048 ** 2, 2)
    assert metadata["dimensionWarning"] == ""


@pytest.mark.parametrize("bad_feet", ["unreadable", "NaN", "7' 13\"", "101", "-2", "2m"])
def test_unusable_imperial_pair_falls_back_to_readable_metric(bad_feet):
    room = native_room("Kitchen", bad_feet, 5)
    room["measurements"] += native_room("Kitchen", 2.78, 2, "m")["measurements"]
    dimensions, metadata = normalize_room_measurements([room])
    assert dimensions[0]["lengthFt"] == pytest.approx(2.78 / 0.3048)
    assert metadata["dimensionWarning"] == ""


@pytest.mark.parametrize("length,expected", [("7'-9\"", 7.75), ("7′9″", 7.75), ("7 ft 9 in", 7.75), ("7.75ft", 7.75)])
def test_feet_inches_notation_is_parsed_by_backend(length, expected):
    dimensions, _ = normalize_room_measurements([native_room("Foyer", length, 4)])
    assert dimensions[0]["lengthFt"] == expected


def test_metric_printed_pair_beats_estimated_feet_and_inference_warns():
    guess = native_room("Toilet", 6, 6, unitSource="inferred", lengthEstimated=True)
    guess["measurements"] += native_room("Toilet", 2, 2, "m")["measurements"]
    dimensions, metadata = normalize_room_measurements([guess])
    assert dimensions[0]["lengthFt"] == pytest.approx(2 / 0.3048)
    assert not metadata["dimensionWarning"]
    guess["measurements"].pop()
    dimensions, metadata = normalize_room_measurements([guess])
    assert dimensions[0]["lengthFt"] == 6
    assert "Toilet: AI-inferred unit, AI-estimated length" in metadata["dimensionWarning"]


def test_unknown_invalid_partial_and_legacy_readings_never_become_zero_or_guessed_feet():
    rooms = [native_room("Unknown", 7, 3.87, "unknown"), native_room("Partial", 10, None),
             native_room("Invalid", "inf", 5), {"room": "Legacy", "lengthFt": 7, "widthFt": 3.87},
             native_room("Bedroom", 2.8, 4, "m")]
    dimensions, metadata = normalize_room_measurements(rooms)
    assert dimensions[0]["lengthFt"] is None and dimensions[3]["widthFt"] is None
    assert dimensions[1]["lengthFt"] == 10 and dimensions[1]["widthFt"] is None
    assert dimensions[2]["lengthFt"] is None
    assert calculate_room_areas(dimensions)[1]["areaSqFt"] is None
    assert dimensions[4]["lengthFt"] == pytest.approx(2.8 / 0.3048)
    assert "Partial: unknown or unusable width/unit" in metadata["dimensionWarning"]


@pytest.mark.parametrize("text,expected", [
    ("Living 7 x 3.87 m", 7 / 0.3048),
    ("Living 7m x 3.87m", 7 / 0.3048),
    ("All dimensions are in metres. Living 7 x 3.87", 7 / 0.3048),
    ("All dimensions in feet. Living 12 x 10", 12),
    ("Living 2.35x1.20 m 7'9\" x 3'11\"", 7.75),
    ("Living 7 x 3.87", None),
    ("All dimensions in metres. All dimensions in feet. Living 7 x 3.87", None),
])
def test_text_only_ingestion_converts_explicit_units_and_never_estimates(text, expected):
    dimensions, metadata = text_room_measurements(text)
    assert dimensions[0]["lengthFt"] == pytest.approx(expected) if expected is not None else dimensions[0]["lengthFt"] is None
    assert bool(metadata["dimensionWarning"]) == (expected is None)
    assert not any(reading["lengthEstimated"] for room in metadata["dimensionEvidence"] for reading in room["measurements"])


def test_malformed_unit_metadata_is_bounded_and_does_not_crash():
    room = native_room("Kitchen", 4, 3, unit={"bad": "unit"}, unitSource=[])
    dimensions, metadata = normalize_room_measurements([None, {}, room])
    assert dimensions == [{"room": "Kitchen", "lengthFt": None, "widthFt": None}]
    assert metadata["dimensionWarning"]


def test_natural_language_apostrophes_are_not_foot_markers():
    dimensions, metadata = normalize_room_measurements([native_room("Living", 7, 3.87, "unknown",
        unitSource="unknown", unitEvidence="Developer's Unit 4's diagram has no unit label")])
    assert dimensions[0]["lengthFt"] is None and metadata["dimensionWarning"]


def test_per_room_labels_override_legend_without_global_unit_assumptions():
    dimensions, metadata = text_room_measurements("All dimensions in metres. Living 12 x 10 ft Kitchen 2.78 x 2")
    assert dimensions[0]["lengthFt"] == 12
    assert dimensions[1]["lengthFt"] == pytest.approx(2.78 / 0.3048)
    assert not metadata["dimensionWarning"]
