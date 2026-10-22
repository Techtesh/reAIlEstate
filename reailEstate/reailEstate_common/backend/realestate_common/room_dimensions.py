"""Rectangular room measurements; these are not certified carpet areas."""

import math
import re


def _printed_unit(text):
    text = str(text or "").lower()
    feet = bool(re.search(r"(?:(?<![a-z])(?:ft|feet|foot)\b|\d\s*['′’](?![a-z]))", text))
    metric = bool(re.search(r"(?<![a-z])(?:m|meters?|metres?)\b", text))
    return "ft" if feet and not metric else "m" if metric and not feet else None


def _length_in_feet(text, unit):
    if not isinstance(text, str) or len(text) > 80 or unit not in {"ft", "m"}:
        return None
    if _printed_unit(text) not in {None, unit}:
        return None
    text = text.strip().lower().replace("′", "'").replace("’", "'").replace("″", '"').replace("”", '"')
    if unit == "m":
        match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:m|meters?|metres?)?", text)
        value = float(match[1]) / 0.3048 if match else None
    else:
        match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:ft|feet|foot|')?(?:\s*-?\s*(\d+(?:\.\d+)?)\s*(?:in|inches|inch|\"))?", text)
        value = None
        if match and (match[2] is None or (float(match[1]).is_integer() and float(match[2]) < 12)):
            value = float(match[1]) + float(match[2] or 0) / 12
    return value if value is not None and math.isfinite(value) and 0.5 <= value <= 100 else None


def dimension_warning(evidence):
    messages = []
    for item in evidence:
        warnings = [warning for warning in item.get("warnings", []) if warning not in item.get("resolvedWarnings", [])]
        if warnings and not item.get("resolved"):
            messages.append(f"{item['room']}: {', '.join(warnings)}")
    return "; ".join(messages)


def normalize_room_measurements(raw_dimensions):
    """Normalize native readings, never assume a bare number is already feet."""
    dimensions, evidence = [], []
    for room in raw_dimensions[:50] if isinstance(raw_dimensions, list) else []:
        if not isinstance(room, dict) or not isinstance(room.get("room"), str) or not room["room"].strip():
            continue
        name = room["room"].strip()[:64]
        measurements = room.get("measurements", [])
        readings, options = [], []
        for raw in measurements[:4] if isinstance(measurements, list) else []:
            if not isinstance(raw, dict):
                continue
            reading = {key: raw.get(key)[:limit] if isinstance(raw.get(key), str) else None
                       for key, limit in (("length", 80), ("width", 80), ("unitEvidence", 500))}
            reading["unit"] = raw.get("unit") if isinstance(raw.get("unit"), str) and raw["unit"] in {"ft", "m", "unknown"} else "unknown"
            reading["unitSource"] = raw.get("unitSource") if isinstance(raw.get("unitSource"), str) and raw["unitSource"] in {"printed", "legend", "inferred", "unknown"} else "unknown"
            for key in ("lengthEstimated", "widthEstimated"):
                reading[key] = raw.get(key) if isinstance(raw.get(key), bool) else True
            units = {_printed_unit(reading[key]) for key in ("length", "width", "unitEvidence")} - {None}
            unit = reading["unit"]
            contradictory = len(units) > 1 or bool(units and unit != "unknown" and unit not in units)
            if not contradictory and unit == "unknown" and len(units) == 1:
                unit = next(iter(units))
            inferred = reading["unitSource"] not in {"printed", "legend"} or unit not in units
            length, width = (None, None) if contradictory else (
                _length_in_feet(reading["length"], unit), _length_in_feet(reading["width"], unit))
            guessed = inferred or reading["lengthEstimated"] or reading["widthEstimated"]
            complete = length is not None and width is not None
            rank = (0 if unit == "ft" else 1) if complete and not guessed else 2 if complete else 3
            options.append(((rank, -(int(length is not None) + int(width is not None))), len(readings),
                            length, width, inferred, contradictory))
            readings.append(reading)
        selected = min(options, key=lambda option: option[0]) if options else None
        length, width = selected[2:4] if selected else (None, None)
        warnings = []
        if selected:
            reading = readings[selected[1]]
            if selected[5]:
                warnings.append("conflicting unit evidence")
            elif selected[4] and (reading["unit"] != "unknown" or length is not None or width is not None):
                warnings.append("AI-inferred unit")
            for axis in ("length", "width"):
                if reading[axis + "Estimated"]:
                    warnings.append(f"AI-estimated {axis}")
        for axis, value in (("length", length), ("width", width)):
            if value is None:
                warnings.append(f"unknown or unusable {axis}/unit")
        dimensions.append({"room": name, "lengthFt": length, "widthFt": width})
        evidence.append({"room": name, "roomIndex": len(dimensions) - 1, "measurements": readings,
                         "selectedIndex": selected[1] if selected else None, "warnings": warnings})
    return dimensions, {"dimensionEvidence": evidence, "dimensionWarning": dimension_warning(evidence)}


_ROOM_LABEL = re.compile(r"\b(?:living(?:\s*/\s*dining)?|dining|kitchen|(?:master\s+)?bed(?:room)?(?:\s*\d+)?|bath(?:room)?(?:\s*\d+)?|toilet|balcony|foyer|internal\s+passage|passage|corridor|utility)\b", re.I)
_VALUE = r'''\d+(?:\.\d+)?(?:\s*(?:ft|feet|foot|['′’])(?:\s*-?\s*\d+(?:\.\d+)?\s*(?:inches|inch|in|["″”]))?|\s*(?:meters?|metres?|m)\b)?'''
_PAIR = re.compile(rf"(?P<length>{_VALUE})\s*[x×]\s*(?P<width>{_VALUE})", re.I)


def text_room_measurements(text):
    """Explicit labels/diagram legends only; the offline path never estimates."""
    legends = re.findall(r"\ball\s+(?:room\s+)?dimensions\s+(?:are\s+)?in\s+(?:meters?|metres?|feet|ft)\b", text, re.I)
    legend_units = {_printed_unit(legend) for legend in legends}
    legend_unit = next(iter(legend_units)) if len(legend_units) == 1 else None
    labels = list(_ROOM_LABEL.finditer(text))
    rooms = []
    for index, label in enumerate(labels):
        block = text[label.end():labels[index + 1].start() if index + 1 < len(labels) else len(text)][:400]
        readings = []
        for pair in _PAIR.finditer(block):
            units = {_printed_unit(pair[axis]) for axis in ("length", "width")} - {None}
            unit = next(iter(units)) if len(units) == 1 else legend_unit if not units else None
            readings.append({"length": pair["length"], "width": pair["width"], "unit": unit or "unknown",
                "unitSource": "printed" if units else "legend" if legend_unit else "unknown",
                "unitEvidence": pair[0] if units else " ".join(legends),
                "lengthEstimated": False, "widthEstimated": False})
        if readings:
            rooms.append({"room": label[0].lower(), "measurements": readings})
    return normalize_room_measurements(rooms)


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
