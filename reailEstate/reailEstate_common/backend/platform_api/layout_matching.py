"""Match and score a buyer's home requirements against individual validated layouts."""

from decimal import Decimal, InvalidOperation

from .layout_configurations import _configuration_name


def _positive(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() and number > 0 else None


def score_layouts(profile, layouts, *, strict):
    home, budget = profile.get("home", {}), profile.get("budget", {})
    requested = {_configuration_name(value) for value in home.get("configurations", [])}
    area = home.get("carpetAreaSqFt", {})
    minimum, preferred = _positive(area.get("minimum")), _positive(area.get("preferred"))
    maximum = _positive(budget.get("allInMaximum") or budget.get("purchaseMaximum"))
    if not layouts:
        return [], "NO_VALIDATED_LAYOUT"
    candidates = []
    for layout in layouts:
        if requested and _configuration_name(layout.configuration_type) not in requested:
            continue
        measured, price = _positive(layout.carpet_area_sqft), _positive(layout.price)
        checks = []
        if minimum is not None:
            checks.append(("carpetAreaSqFt", measured, measured is not None and measured >= minimum))
        bathrooms = home.get("bathroomsMinimum")
        if bathrooms and home.get("bathroomsIsHardConstraint", True):
            checks.append(("bathrooms", layout.bathroom_count,
                           layout.bathroom_count is not None and layout.bathroom_count >= bathrooms))
        if maximum is not None and budget.get("isHardConstraint"):
            checks.append(("price", price, price is not None and price <= maximum))
        unverified = [field for field, value, _ in checks if value is None]
        violated = [field for field, value, passed in checks if value is not None and not passed]
        space_score = float(min(Decimal("1"), measured / preferred)) if measured is not None and preferred is not None else None
        budget_score = None
        if maximum is not None and price is not None:
            if price <= maximum:
                budget_score = float(max(Decimal("0.5"), Decimal("1") - (maximum - price) / maximum * Decimal("0.5")))
            else:
                budget_score = float(max(Decimal("0"), Decimal("1") - (price - maximum) / maximum))
        candidates.append({"layout": layout, "values": {"budget": budget_score, "space": space_score},
                           "unverifiedConstraints": unverified, "violatedConstraints": violated})
    if not candidates:
        return [], "CONFIGURATION_UNAVAILABLE"
    if strict:
        for field, reason in (("carpetAreaSqFt", "BELOW_HARD_CARPET_AREA"),
                              ("bathrooms", "INSUFFICIENT_BATHROOMS"), ("price", "OVER_HARD_BUDGET")):
            passing = [item for item in candidates if field not in item["unverifiedConstraints"] + item["violatedConstraints"]]
            if not passing:
                missing = any(field in item["unverifiedConstraints"] for item in candidates)
                return [], "LAYOUT_REQUIRED_DATA_UNAVAILABLE" if missing else reason
            candidates = passing
    return candidates, None


def layout_summary(candidate, score):
    layout = candidate["layout"]
    return {
        "id": str(layout.pk), "name": layout.display_title,
        "configurationType": _configuration_name(layout.configuration_type),
        "carpetAreaSqFt": str(layout.carpet_area_sqft) if layout.carpet_area_sqft is not None else None,
        "bathrooms": layout.bathroom_count, "price": str(layout.price) if layout.price is not None else None,
        "score": round(score, 2), "unverifiedConstraints": candidate["unverifiedConstraints"],
        "violatedConstraints": candidate["violatedConstraints"],
    }
