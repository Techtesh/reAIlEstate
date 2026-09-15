"""Local, deterministic plan identification. No model or network dependency."""

import hashlib
import re


RULE_PROFILE_ID = "rules-only"
RULE_VERSION = "plan-identification-rules-1.0"


def rule_selection():
    return {
        "profileId": RULE_PROFILE_ID,
        "provider": "rules",
        "model": "",
        "configurationHash": hashlib.sha256(RULE_VERSION.encode()).hexdigest(),
    }


class RuleBasedPlanProvider:
    code = "rules"
    model = ""
    profile_id = RULE_PROFILE_ID


def classify_plan_scope(text):
    floor = bool(re.search(r"\b(?:typical\s*floor|floor\s*(?:plan|layout)|tower\s*plan|wing\s*plan)\b", text, re.I))
    flat = bool(re.search(r"\b(?:unit|flat|apartment)\s*(?:plan|layout)\b", text, re.I))
    if floor and flat:
        return "unknown"  # Mixed page: do not claim a single scope.
    if floor:
        return "floor"
    if flat:
        return "flat"
    if re.search(r"\b[1-6](?:\.5)?\s*BHK\b", text, re.I) and len(set(re.findall(
        r"\b(?:living|bedroom|kitchen|toilet|bathroom|balcony)\b", text.casefold()
    ))) >= 2:
        return "flat"
    return "unknown"


def plan_source_kind(plan):
    """Identify whether a layout came pre-cut from the source or was derived later."""
    asset = getattr(plan, "asset", None)
    if asset is not None and asset.asset_type == "unit_crop":
        return "generated_unit_crop"
    document = getattr(plan, "document", None)
    if document is not None and document.declared_media_type in {"image/png", "image/jpeg"}:
        return "precut_source"
    return "document_page"


def plan_display_priority(plan):
    """Sort source crops ahead of generated and page-level candidates."""
    source_rank = {"precut_source": 0, "generated_unit_crop": 1, "document_page": 2}[plan_source_kind(plan)]
    scope_rank = {"flat": 0, "floor": 1, "unknown": 2}.get(plan.plan_scope, 3)
    return (scope_rank, source_rank, -float(plan.confidence), plan.page_number or 0, str(plan.id))