"""Pure unit-region validation, floor labels and image/CV geometry shared by services.

Depends only on the standard library, Pillow, OpenCV and NumPy; never imports
ORM models, storage, policies or services. Persistence and UI consume this leaf.
"""

import io
import math
import re
from dataclasses import replace
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

__all__ = [
    "MAX_UNITS", "MIN_BOX_AREA", "MAX_BOX_AREA", "UnitRegion",
    "floor_applicability", "with_floor_applicability", "validate_regions",
    "_content_mask", "_red_markers", "_dominant_markers", "_regions_from_seeds",
    "_radial_seeds", "_colored_regions", "segment_units_script", "crop_region",
]


MAX_UNITS = 40
MIN_BOX_AREA = 0.01
MAX_BOX_AREA = 0.70


@dataclass(frozen=True)
class UnitRegion:
    label: str
    x: float
    y: float
    width: float
    height: float
    confidence: float
    evidence: str
    floors: list[str]


def floor_applicability(text):
    """Keep visibly stated floor ranges as labels; do not infer or expand them."""
    if not isinstance(text, str):
        return []
    matches = re.findall(
        r"\b(?:\d{1,3}(?:ST|ND|RD|TH)(?:\s*(?:TO|[-–&,])\s*\d{1,3}(?:ST|ND|RD|TH))*(?:\s*(?:,|&|AND)\s*)?)+\s+FLOOR(?:\s+PLAN)?\b",
        text.upper(),
    )
    return [re.sub(r"\s+", " ", match).strip()[:250] for match in matches[:20]]


def with_floor_applicability(regions, labels):
    return [replace(region, floors=region.floors or labels) for region in regions]


def validate_regions(raw_regions, expected_count=None, *, allow_overlap=False):
    if not isinstance(raw_regions, list) or len(raw_regions) > MAX_UNITS:
        raise ValueError("INVALID_UNIT_REGIONS")
    regions = []
    for index, item in enumerate(raw_regions, 1):
        if not isinstance(item, dict):
            continue
        try:
            x, y = float(item["x"]), float(item["y"])
            width, height = float(item["width"]), float(item["height"])
            confidence = float(item.get("confidence", 0))
        except (KeyError, TypeError, ValueError):
            continue
        values = (x, y, width, height, confidence)
        if not all(math.isfinite(value) for value in values):
            continue
        area = width * height
        if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1.000001 or y + height > 1.000001:
            continue
        if not MIN_BOX_AREA <= area <= MAX_BOX_AREA:
            continue
        floors = item.get("floors", [])
        if not isinstance(floors, list):
            floors = []
        regions.append(UnitRegion(
            label=str(item.get("label") or f"Unit {index}")[:64], x=x, y=y, width=width, height=height,
            confidence=max(0, min(1, confidence)), evidence=str(item.get("evidence", ""))[:500],
            floors=[str(value)[:64] for value in floors[:50] if str(value).strip()],
        ))
    if len(regions) != len(raw_regions):
        raise ValueError("INVALID_UNIT_REGIONS")
    if not allow_overlap:
        for index, first in enumerate(regions):
            for second in regions[index + 1:]:
                intersection = max(0, min(first.x + first.width, second.x + second.width) - max(first.x, second.x)) * max(
                    0, min(first.y + first.height, second.y + second.height) - max(first.y, second.y))
                smaller = min(first.width * first.height, second.width * second.height)
                if smaller and intersection / smaller > 0.65:
                    raise ValueError("OVERLAPPING_UNIT_REGIONS")
    if expected_count is not None and len(regions) != expected_count:
        # An expected count is a quality gate, never an instruction to invent boxes.
        raise ValueError("UNIT_COUNT_MISMATCH")
    return regions


def _content_mask(rgb):
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    return ((saturation > 18) & (value < 252)).astype(np.uint8)


def _red_markers(rgb):
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    low = cv2.inRange(hsv, (0, 80, 80), (12, 255, 255))
    high = cv2.inRange(hsv, (168, 80, 80), (179, 255, 255))
    mask = cv2.morphologyEx(low | high, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask)
    height, width = mask.shape
    result = []
    for index in range(1, count):
        x, y, w, h, area = stats[index]
        ratio = w / max(1, h)
        if 8 <= w <= width * 0.08 and 8 <= h <= height * 0.08 and 0.55 <= ratio <= 1.8 and area >= 15:
            result.append((float(centroids[index][0]), float(centroids[index][1]), (x, y, w, h)))
    # Merge multiple red components belonging to one circled marker.
    merged = []
    for center_x, center_y, box in sorted(result, key=lambda item: (item[0], item[1])):
        if any((center_x - old[0]) ** 2 + (center_y - old[1]) ** 2 < max(box[2], box[3], old[2][2], old[2][3]) ** 2 * 2.25 for old in merged):
            continue
        merged.append((center_x, center_y, box))
    return merged


def _dominant_markers(markers):
    if len(markers) < 2:
        return markers
    widths = np.array([item[2][2] for item in markers])
    heights = np.array([item[2][3] for item in markers])
    median_width, median_height = np.median(widths), np.median(heights)
    selected = [item for item in markers if 0.8 <= item[2][2] / median_width <= 1.25
                and 0.8 <= item[2][3] / median_height <= 1.25]
    return selected if len(selected) >= 2 else markers


def _regions_from_seeds(rgb, seeds, method):
    height, width = rgb.shape[:2]
    mask = _content_mask(rgb)
    # Keep the dominant architectural component and discard remote title tables/logos.
    kernel = max(5, round(min(width, height) * 0.004))
    if kernel % 2 == 0:
        kernel += 1
    connected = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((kernel, kernel), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(connected)
    component = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA])) if count > 1 else 0
    component_mask = labels == component if component else mask.astype(bool)
    ys, xs = np.nonzero(component_mask & mask.astype(bool))
    if len(xs) < 100:
        return []
    points = np.column_stack((xs, ys)).astype(np.float32)
    seed_array = np.array([(item[0], item[1]) for item in seeds], dtype=np.float32)
    assignments = np.empty(len(points), dtype=np.int16)
    for start in range(0, len(points), 250_000):
        block = points[start:start + 250_000]
        assignments[start:start + len(block)] = np.argmin(
            ((block[:, None, :] - seed_array[None, :, :]) ** 2).sum(axis=2), axis=1)
    padding = max(12, round(min(width, height) * 0.02))
    ordered = sorted(enumerate(seeds), key=lambda item: (item[1][1], item[1][0]))
    raw = []
    for output_index, (seed_index, _) in enumerate(ordered, 1):
        assigned = points[assignments == seed_index]
        if len(assigned) < 100:
            continue
        x0, y0 = np.percentile(assigned, 1, axis=0)
        x1, y1 = np.percentile(assigned, 99, axis=0)
        x0, y0 = max(0, int(x0) - padding), max(0, int(y0) - padding)
        x1, y1 = min(width, int(x1) + padding), min(height, int(y1) + padding)
        raw.append({"label": f"Unit {output_index:02d}", "x": x0 / width, "y": y0 / height,
                    "width": (x1 - x0) / width, "height": (y1 - y0) / height,
                    "confidence": 0.68 if method == "numbered_unit_markers" else 0.38,
                    "evidence": method, "floors": []})
    return raw


def _radial_seeds(mask):
    """Fallback for colored radial floor plans without numbered red unit markers."""
    ys, xs = np.nonzero(mask)
    if len(xs) < 100:
        return []
    center_x, center_y = float(np.median(xs)), float(np.median(ys))
    dx, dy = xs - center_x, ys - center_y
    radius = np.hypot(dx, dy)
    keep = radius > np.percentile(radius, 50)
    angles = (np.arctan2(dy[keep], dx[keep]) + 2 * np.pi) % (2 * np.pi)
    histogram, _ = np.histogram(angles, bins=180)
    smooth = np.convolve(np.r_[histogram[-8:], histogram, histogram[:8]], np.ones(17), mode="same")[8:-8]
    threshold = max(5, np.percentile(smooth, 60))
    active = smooth >= threshold
    groups, current = [], []
    for index, value in enumerate(active):
        if value:
            current.append(index)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    if len(groups) > 1 and groups[0][0] == 0 and groups[-1][-1] == 179:
        groups[0] = groups[-1] + groups[0]
        groups.pop()
    groups = [group for group in groups if len(group) >= 5]
    seeds = []
    outer = np.percentile(radius, 72)
    for group in groups[:MAX_UNITS]:
        angle = np.mean([(value % 180) * 2 * np.pi / 180 for value in group])
        seeds.append((center_x + math.cos(angle) * outer, center_y + math.sin(angle) * outer, None))
    return seeds if 2 <= len(seeds) <= MAX_UNITS else []


def _colored_regions(rgb):
    """Find disconnected colored dwelling fills commonly used in architectural plans."""
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    height, width = hsv.shape[:2]
    mask = ((hsv[:, :, 1] > 20) & (hsv[:, :, 2] > 80) & (hsv[:, :, 2] < 254)).astype(np.uint8)
    kernel = max(5, round(min(width, height) * 0.005))
    if kernel % 2 == 0:
        kernel += 1
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((kernel, kernel), np.uint8))
    count, _, stats, centroids = cv2.connectedComponentsWithStats(mask)
    candidates = []
    for index in range(1, count):
        x, y, box_width, box_height, pixels = stats[index]
        box_area = box_width * box_height / (width * height)
        fill_ratio = pixels / max(1, box_width * box_height)
        if 0.006 <= box_area <= 0.24 and box_width >= width * 0.06 and box_height >= height * 0.06 and fill_ratio >= 0.08:
            candidates.append((x, y, box_width, box_height, float(centroids[index][0]), float(centroids[index][1])))
    if not 2 <= len(candidates) <= MAX_UNITS:
        return []
    center_x = np.median([item[4] for item in candidates])
    center_y = np.median([item[5] for item in candidates])
    # Stable clockwise order from the top; labels are intentionally generic because
    # local CV does not reliably OCR arbitrary flat numbers.
    candidates.sort(key=lambda item: (math.atan2(item[4] - center_x, -(item[5] - center_y)) + 2 * math.pi) % (2 * math.pi))
    padding = max(8, round(min(width, height) * 0.018))
    raw = []
    for index, (x, y, box_width, box_height, _, _) in enumerate(candidates, 1):
        x0, y0 = max(0, x - padding), max(0, y - padding)
        x1, y1 = min(width, x + box_width + padding), min(height, y + box_height + padding)
        raw.append({"label": f"Unit {index:02d}", "x": x0 / width, "y": y0 / height,
                    "width": (x1 - x0) / width, "height": (y1 - y0) / height,
                    "confidence": 0.78, "evidence": "disconnected_colored_dwelling_region", "floors": []})
    return raw


def segment_units_script(image_bytes, expected_count=None):
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    rgb = np.asarray(image)
    height, width = rgb.shape[:2]
    mask = _content_mask(rgb)
    markers = _dominant_markers(_red_markers(rgb))
    radial = _radial_seeds(mask)
    colored = _colored_regions(rgb)
    candidates = [
        ("disconnected_colored_regions", colored),
        ("numbered_unit_markers", _regions_from_seeds(rgb, markers, "numbered_unit_markers") if len(markers) >= 2 else []),
        ("radial_color_geometry", _regions_from_seeds(rgb, radial, "radial_color_geometry") if len(radial) >= 2 else []),
    ]
    if expected_count is not None:
        candidates.sort(key=lambda item: (len(item[1]) != expected_count,
                                          {"numbered_unit_markers": 0, "disconnected_colored_regions": 1,
                                           "radial_color_geometry": 2}[item[0]]))
    else:
        candidates.sort(key=lambda item: ({"numbered_unit_markers": 0, "disconnected_colored_regions": 1,
                                           "radial_color_geometry": 2}[item[0]], -len(item[1])))
    errors = []
    for method, raw in candidates:
        if len(raw) < 2:
            continue
        try:
            regions = validate_regions(raw, expected_count)
            return regions, {"method": method, "regionCount": len(regions), "expectedCount": expected_count,
                             "signals": {"coloredRegions": len(colored),
                                         "numberedMarkers": len(markers), "radialRegions": len(radial)}}
        except ValueError as exc:
            errors.append(str(exc))
    raise ValueError(errors[0] if errors else "SCRIPT_SEGMENTATION_INSUFFICIENT_SIGNALS")


def crop_region(image, region):
    width, height = image.size
    box = (max(0, round(region.x * width)), max(0, round(region.y * height)),
           min(width, round((region.x + region.width) * width)),
           min(height, round((region.y + region.height) * height)))
    crop = image.crop(box).convert("RGB")
    output = io.BytesIO()
    crop.save(output, format="PNG", optimize=True)
    return output.getvalue(), box, crop.size