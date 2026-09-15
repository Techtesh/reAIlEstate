"""Closed-taxonomy amenity matching with controlled typo tolerance."""

from dataclasses import dataclass
import re
import unicodedata


AMENITY_ALIASES = {
    "garden": ("garden", "gardens", "landscape garden", "landscaped garden"),
    "miyawaki": ("miyawaki", "miyawaki forest"),
    "walking_track": ("walking track", "walkway", "walkway path", "walking path"),
    "jogging_track": ("jogging track", "joggers track"),
    "multipurpose_lawn": ("multipurpose lawn", "multi purpose lawn"),
    "pergola": ("pergola",),
    "climber": ("climber", "climbing wall"),
    "gazebo": ("gazebo",),
    "flower_bed": ("flower bed", "flower beds"),
    "sandpit": ("sandpit", "sand pit"),
    "clubhouse": ("clubhouse", "club house"),
    "gymnasium": ("gym", "gymnasium", "fully equipped gym"),
    "swimming_pool": ("swimming pool", "swimmingpool"),
    "children_play_area": (
        "children play area", "childrens play area", "children's play area",
        "kids play area", "kid play area", "toddlers play area",
    ),
    "senior_citizen_area": ("senior citizen area", "senior citizens area", "senior citizen sit out"),
    "amphitheatre": ("amphitheatre", "amphitheater", "amphitheater sitting", "amphitheatre sitting"),
    "multipurpose_court": ("multipurpose court", "multi purpose court"),
    "yoga_deck": ("yoga deck", "yoga area", "yoga zone", "yoga"),
    "library_reading_area": ("library", "reading area", "reading room", "reding area", "library and reading area"),
    "steam_room": ("steam room",),
    "massage_room": ("massage room",),
    "waiting_area": ("waiting area",),
    "multipurpose_room": ("multipurpose room", "multipurpose hall", "multi purpose hall", "multi purpose room"),
    "guest_room": ("guest room",),
    "indoor_games": ("indoor game", "indoor games", "indoor games room"),
    "net_cricket_pitch": ("net cricket pitch", "cricket net", "cricket nets"),
    "jacuzzi": ("jacuzzi",),
    "art_room": ("art room", "arts room"),
    "security": ("security", "security cabin"),
    "parking": ("parking", "car parking", "podium parking"),
    "creche": ("creche", "crèche", "baby creche", "baby crèche"),
}

# Short or generic aliases must match exactly; fuzzy matching these causes false positives.
_NO_FUZZY = {"gym", "yoga", "garden", "gardens", "parking", "security", "library", "climber", "gazebo", "sandpit", "jacuzzi"}
_NEGATIONS = {"no", "not", "without", "unavailable", "absent"}
_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class AmenityMatch:
    code: str
    text: str
    start: int
    end: int
    method: str


def _fold(value):
    return "".join(
        character for character in unicodedata.normalize("NFKD", str(value).casefold())
        if not unicodedata.combining(character)
    )


def _tokens(value):
    return [match.group(0) for match in _TOKEN_RE.finditer(_fold(value))]


def _distance_at_most_one(left, right):
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) <= 1
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    index = errors = 0
    for character in longer:
        if index < len(shorter) and character == shorter[index]:
            index += 1
        else:
            errors += 1
            if errors > 1:
                return False
    return True


def find_amenities(text):
    original = str(text or "")
    folded = _fold(original)
    token_matches = list(_TOKEN_RE.finditer(folded))
    values = [match.group(0) for match in token_matches]
    found = []
    seen = set()
    for code, aliases in AMENITY_ALIASES.items():
        for alias in aliases:
            expected = _tokens(alias)
            if not expected or len(expected) > len(values):
                continue
            fuzzy_allowed = alias not in _NO_FUZZY
            for index in range(len(values) - len(expected) + 1):
                actual = values[index:index + len(expected)]
                differences = [position for position, pair in enumerate(zip(actual, expected)) if pair[0] != pair[1]]
                method = "exact_alias"
                if differences:
                    if not fuzzy_allowed or len(differences) != 1:
                        continue
                    position = differences[0]
                    if len(expected[position]) < 5 or not _distance_at_most_one(actual[position], expected[position]):
                        continue
                    method = "fuzzy_alias"
                if any(token in _NEGATIONS for token in values[max(0, index - 3):index]):
                    continue
                start = token_matches[index].start()
                end = token_matches[index + len(expected) - 1].end()
                key = (code, start, end)
                if key in seen:
                    continue
                seen.add(key)
                found.append(AmenityMatch(code, original[start:end], start, end, method))
    found.sort(key=lambda item: (item.start, -(item.end - item.start), item.code))
    return found


def has_amenity_terms(text):
    return bool(find_amenities(text))
