"""Optional general-page visual claims; always unreviewed and evidence-linked."""

from django.conf import settings

from .artifact_storage import storage
from .integrations import InvalidProviderResponse, ProviderUnavailable
from .ingestion_runtime import event


FIELDS = ["name", "location", "developerName", "reraNumbers", "landHoldingSqFt", "totalTowers",
          "totalResidentialUnits", "totalParkings", "offeredPriceInr", "offers", "possession", "metroClaim", "localClaim",
          "amenities_nature", "amenities_social", "amenities_unclassified"]
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["facts"], "properties": {
    "facts": {"type": "array", "items": {"type": "object", "additionalProperties": False,
        "required": ["fieldCode", "value", "evidenceText"], "properties": {
            "fieldCode": {"type": "string", "enum": FIELDS}, "value": {"type": "string"},
            "evidenceText": {"type": "string"}}}}}}
SCHEMA["required"].append("towers")
SCHEMA["properties"]["towers"] = {"type": "array", "items": {
    "type": "object", "additionalProperties": False,
    "required": ["tower", "residentialUnits", "unitsPerFloor", "lifts", "evidenceText"],
    "properties": {"tower": {"type": "string"}, "residentialUnits": {"type": ["integer", "null"]},
                   "unitsPerFloor": {"type": ["integer", "null"]}, "lifts": {"type": ["integer", "null"]},
                   "evidenceText": {"type": "string"}},
}}


def visual_project_claims(workflow, provider):
    from .floor_plan_vision import provider_metadata
    if getattr(provider, "code", "rules") == "rules":
        return {"status": "not_requested", "claims": [], "pages": []}
    if not hasattr(provider, "analyze_project_page"):
        return {"status": "unavailable", "claims": [], "pages": [], "reasonCode": "PROJECT_VISION_NOT_SUPPORTED"}
    claims, pages, towers = [], [], []
    assets = list(workflow.floor_plan_assets.all())
    # Dedicated floor-plan extraction already evaluates these assets with the richer plan schema.
    # Do not pay for a second general-claims request over the same image.
    assets = [asset for asset in assets if asset.source_bounds.get("category") not in {"layouts", "floor-plans"}]
    # If the visual budget is exhausted, retain dwelling layouts first because
    # they drive configuration, room, area, and comparison calculations.
    category_rank = {"layouts": 0, "floor-plans": 1, "amenities": 2, "general": 3, "unclassified": 4}
    assets.sort(key=lambda asset: (
        category_rank.get(asset.source_bounds.get("category"), 5), asset.page_number or 0, str(asset.id)
    ))
    assets = assets[:settings.PROJECT_VISION_MAX_PAGES]
    for index, asset in enumerate(assets, 1):
        event("project_page_request", pageNumber=asset.page_number, imageIndex=index, imageTotal=len(assets))
        try:
            with storage.open(asset.object_key) as image:
                result = provider.analyze_project_page(image.read(), asset.media_type, SCHEMA)
            for fact in result["facts"][:100]:
                if not fact["value"].strip() or not fact["evidenceText"].strip():
                    continue
                claims.append({"fieldCode": fact["fieldCode"], "value": fact["value"][:240],
                    "documentId": str(workflow.document_id), "pageNumber": asset.page_number,
                    "evidenceText": fact["evidenceText"][:500], "source": "visual_model", "reviewState": "unreviewed",
                    "assetId": str(asset.id), "provider": provider.code, "model": getattr(provider, "model", "")})
            for tower in result.get("towers", [])[:50]:
                if not tower.get("tower") or not tower.get("evidenceText"):
                    continue
                towers.append({"tower": tower["tower"][:100],
                    **{key: tower.get(key) if type(tower.get(key)) is int and tower[key] >= 0 else None
                       for key in ("residentialUnits", "unitsPerFloor", "lifts")},
                    "evidenceText": tower["evidenceText"][:500], "documentId": str(workflow.document_id),
                    "pageNumber": asset.page_number, "source": "visual_model", "validationStatus": "unverified"})
            pages.append({"pageNumber": asset.page_number, "status": "completed", **provider_metadata(provider)})
        except (ProviderUnavailable, InvalidProviderResponse, OSError, ValueError, KeyError, TypeError) as exc:
            pages.append({"pageNumber": asset.page_number, "status": "failed",
                          "errorCode": getattr(exc, "code", type(exc).__name__),
                          "visionAttempts": getattr(exc, "attempts", [])})
    return {"status": "partial_failure" if any(item["status"] == "failed" for item in pages) else "completed",
            "claims": claims, "pages": pages, "towers": towers}
