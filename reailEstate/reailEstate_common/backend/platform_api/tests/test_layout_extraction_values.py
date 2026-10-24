from copy import deepcopy
from types import SimpleNamespace

import pytest

from platform_api.layout_extraction_admin import RoomForm, RoomFormSet, corrected_dimensions, corrected_dimension_metadata


def test_filling_missing_dimension_keeps_other_extracted_precision_and_resolves_warning():
    plan = SimpleNamespace(dimensions=[
        {"room": "Bedroom", "lengthFt": 11.583333333333334, "widthFt": 9.5},
        {"room": "ENT.W.PASS", "lengthFt": 3.5, "widthFt": None},
    ], raw_extraction={"dimensionEvidence": [{"room": "ENT.W.PASS", "roomIndex": 1,
        "warnings": ["unknown or unusable width/unit"]}]})
    original = deepcopy(plan)
    values = {"rooms-TOTAL_FORMS": 2, "rooms-INITIAL_FORMS": 2,
        "rooms-0-source_index": 0, "rooms-0-room": "Bedroom", "rooms-0-length_ft": "11.583333333333334", "rooms-0-width_ft": "9.5",
        "rooms-1-source_index": 1, "rooms-1-room": "ENT.W.PASS", "rooms-1-length_ft": "3.5", "rooms-1-width_ft": "4"}
    rooms = RoomFormSet(values, prefix="rooms")
    assert rooms.is_valid(), rooms.errors
    dimensions = corrected_dimensions(plan, rooms)
    assert dimensions[0]["lengthFt"] == original.dimensions[0]["lengthFt"]
    assert dimensions[1]["widthFt"] == 4 and dimensions[1]["areaSqFt"] == 14
    assert corrected_dimension_metadata(plan, rooms)["dimensionWarning"] == ""
    assert plan.dimensions == original.dimensions and plan.raw_extraction == original.raw_extraction


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0", "-1", "1001"])
def test_room_measurements_still_reject_nonfinite_and_out_of_range_values(value):
    form = RoomForm({"room": "Kitchen", "length_ft": value, "width_ft": "4"})
    assert not form.is_valid()
    assert "length_ft" in form.errors
