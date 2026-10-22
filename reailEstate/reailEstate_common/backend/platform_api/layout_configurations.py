import re

from django.db.models import Value
from django.db.models.functions import Replace, Trim, Upper

from .layout_pricing import pricing_layouts


CONFIGURATION = re.compile(r"^(?:[1-6](?:\.5)?\s*BHK|1\s*RK|studio)$", re.IGNORECASE)


def _configuration_name(value):
    name = str(value or "").strip()
    if not name:
        return ""
    if CONFIGURATION.fullmatch(name):
        return name.upper().replace(" ", "")
    return name[:128]


def matching_layouts():
    return pricing_layouts(can_review=False).alias(
        configuration_key=Upper(Replace(Trim("configuration_type"), Value(" "), Value(""))),
    )


def project_layout_configurations(project, *, can_review=False):
    """Keep one row per eligible layout, including aliases and measured areas."""
    layouts = []
    plans = pricing_layouts(can_review=can_review).filter(
        pricing_project_id=project.pk,
    )
    for plan in plans:
        layouts.append({
            "id": str(plan.pk), "name": plan.display_title,
            "configuration_type": _configuration_name(plan.configuration_type),
            "carpet_area_min": plan.carpet_area_sqft, "carpet_area_max": plan.carpet_area_sqft,
            "room_rectangle_sum_sq_ft": plan.room_rectangle_sum_sqft,
            "price_min": plan.price, "price_max": plan.price, "currency": "INR",
            "bathrooms": plan.bathroom_count, "balconies": plan.balcony_count, "parking_spaces": None,
            "room_dimensions": {}, "fact_set_version": 0,
        })
    return sorted(layouts, key=lambda item: (item["name"].casefold(), item["id"]))
