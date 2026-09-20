from platform_api.amenity_matching import find_amenities


def codes(text):
    return {match.code for match in find_amenities(text)}


def test_aliases_and_controlled_typos_map_to_closed_taxonomy():
    assert codes("Swiming pool, amphitheater sitting, walkway path and childrens play area") == {
        "swimming_pool", "amphitheatre", "walking_track", "children_play_area",
    }


def test_short_generic_tokens_are_not_fuzzy_matched():
    assert "gymnasium" not in codes("Gum room")
    assert "swimming_pool" not in codes("Polling booth")


def test_negated_amenities_are_not_claimed():
    assert "swimming_pool" not in codes("No swimming pool is provided")
    assert "gymnasium" not in codes("Apartments without a gym")


def test_acme_and_ace_amenity_aliases():
    assert codes(
        "Gazebo, multipurpose court, kids play area, flower bed, sand pit, massage room, "
        "waiting area, net cricket pitch, jacuzzi, art room and library"
    ) == {
        "gazebo", "multipurpose_court", "children_play_area", "flower_bed", "sandpit",
        "massage_room", "waiting_area", "net_cricket_pitch", "jacuzzi", "art_room",
        "library_reading_area",
    }
