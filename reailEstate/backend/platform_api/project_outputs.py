"""Versioned extraction reports. Unknown/conflicting values are never silently filled."""

import csv
import io
import json
import math
import re
from collections import defaultdict

from django.core.files.base import ContentFile
from django.utils import timezone

from .artifact_storage import storage
from .amenity_matching import find_amenities
from .project_enrichment import backend, fetch, journey_link, source_url


VERSION = "project-output-1.0"
RERA = re.compile(r"\b(?:P\d{10,12}|P[MR]\d{11,13})\b", re.I)
NUMBER = r"(\d[\d,]*(?:\.\d+)?)"
LAND = re.compile(r"(?:total\s+land(?:\s+holding)?|land\s+area|project\s+(?:land\s+)?area|land\s+parcel)\s*[:=-]?\s*" + NUMBER + r"\s*(acres?|hectares?|ha\b|sq\.?\s*m(?:eters?|etres?)?|sqm|sq\.?\s*ft|sqft)", re.I)
COUNT_PATTERNS = {
    "totalTowers": re.compile(r"(?:total\s+(?:number\s+of\s+)?towers|number\s+of\s+towers)\s*[:=-]?\s*(\d[\d,]*)", re.I),
    "totalResidentialUnits": re.compile(r"(?:total\s+(?:number\s+of\s+)?(?:residential\s+)?(?:units|apartments|flats)|number\s+of\s+residential\s+units)\s*[:=-]?\s*(\d[\d,]*)", re.I),
    "totalParkings": re.compile(r"(?:total\s+(?:number\s+of\s+)?parking(?:s|\s+spaces)?|number\s+of\s+parkings)\s*[:=-]?\s*(\d[\d,]*)", re.I),
}
LABELS = {
    "name": r"(?:project\s*name|development\s*name)",
    "location": r"(?:project\s*location|location|address|locality)",
    "developerName": r"(?:developer|promoter|builder)(?:\s*name)?",
    "offers": r"(?:special\s*offers?|offers?|launch\s*offer)",
    "possession": r"(?:possession|handover|completion)(?:\s*date)?",
    "metroClaim": r"(?:nearest\s*metro(?:\s*station)?)",
    "localClaim": r"(?:nearest\s*(?:local|railway)(?:\s*station)?)",
}
AMENITIES = {
    "nature": {
        "garden": r"\b(?:landscap(?:e|ed)\s+)?gardens?\b", "miyawaki": r"\bmiyawaki\b",
        "walking_track": r"\bwalking\s*track\b", "jogging_track": r"\bjogging\s*track\b",
        "multipurpose_lawn": r"\bmulti[ -]?purpose\s+lawn\b", "pergola": r"\bpergola\b",
        "climber": r"\bclimber\b", "gazebo": r"\bgazebo\b", "flower_bed": r"\bflower\s+beds?\b",
        "sandpit": r"\bsand\s*pit\b",
    },
    "social": {
        "clubhouse": r"\bclub\s*house\b", "gymnasium": r"\b(?:fully\s+equipped\s+)?gym(?:nasium)?\b",
        "swimming_pool": r"\bswimming\s*pool\b",
        "children_play_area": r"\b(?:children(?:'s)?|kids?)\s*play\s*area\b",
        "senior_citizen_area": r"\bsenior\s+citizen(?:s?'?)?\s+(?:area|zone|sit[ -]?out)\b",
        "amphitheatre": r"\bamphitheat(?:re|er)\b", "multipurpose_court": r"\bmulti[ -]?purpose\s+court\b",
        "yoga_deck": r"\byoga\s+deck\b", "library_reading_area": r"\b(?:library|reading\s+area|reding\s+area)\b",
        "steam_room": r"\bsteam\s+room\b", "multipurpose_room": r"\bmulti[ -]?purpose\s+room\b",
        "guest_room": r"\bguest\s+room\b", "indoor_games": r"\bindoor\s+games?\b",
        "massage_room": r"\bmassage\s+room\b", "waiting_area": r"\bwaiting\s+area\b",
        "net_cricket_pitch": r"\b(?:net\s+cricket\s+pitch|cricket\s+nets?)\b",
        "jacuzzi": r"\bjacuzzi\b", "art_room": r"\barts?\s+room\b",
    },
    "unclassified": {
        "security": r"\bsecurity\b", "parking": r"\bparking\b",
        "creche": r"\b(?:baby\s+)?cr[eè]che\b",
    },
}


def numeric(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError):
        return None


def field(candidates):
    unique = {json.dumps(item["value"], sort_keys=True) for item in candidates}
    return {"value": candidates[0]["value"] if len(unique) == 1 else None,
            "status": "conflict" if len(unique) > 1 else "extracted" if unique else "unknown",
            "candidates": candidates}


def extract_fields(pages, document_id):
    values = defaultdict(list)

    def add(code, value, page, match):
        item = {"value": value, "documentId": str(document_id), "pageNumber": page.page_number,
                "evidenceText": match.group(0)[:500], "source": "document", "reviewState": "unreviewed"}
        if item not in values[code]:
            values[code].append(item)

    towers = []
    for page in pages:
        text = page.text
        for code, label in LABELS.items():
            pattern = re.compile(r"(?:^|\n)\s*" + label + r"\s*[:=-]\s*([^\n\r|]{2,240})", re.I)
            for match in pattern.finditer(text):
                add(code, match.group(1).strip(), page, match)
        for match in RERA.finditer(text):
            add("reraNumbers", match.group(0).upper(), page, match)
        for code, pattern in COUNT_PATTERNS.items():
            for match in pattern.finditer(text):
                add(code, int(match.group(1).replace(",", "")), page, match)
        for match in LAND.finditer(text):
            amount = float(match.group(1).replace(",", ""))
            unit = re.sub(r"[.\s]", "", match.group(2).lower())
            factor = 43560 if unit.startswith("acre") else 107639.104167 if unit.startswith(("hectare", "ha")) else 10.7639104167 if unit.startswith(("sqm",)) else 1
            add("landHoldingSqFt", round(amount * factor, 2), page, match)
        for match in re.finditer(r"(?:price|starting\s*(?:from|at)|offered\s*at)\s*[:=-]?\s*(?:INR|Rs\.?|₹)\s*" + NUMBER + r"\s*(crore|cr\b|lakh|lac|million)?", text, re.I):
            multiplier = {"crore": 1e7, "cr": 1e7, "lakh": 1e5, "lac": 1e5, "million": 1e6}.get((match.group(2) or "").lower(), 1)
            add("offeredPriceInr", float(match.group(1).replace(",", "")) * multiplier, page, match)
        amenity_categories = {code: category for category, terms in AMENITIES.items() for code in terms}
        for match in find_amenities(text):
            item = {"value": match.code, "documentId": str(document_id), "pageNumber": page.page_number,
                    "evidenceText": match.text[:500], "source": "document", "reviewState": "unreviewed",
                    "matchMethod": match.method}
            code = "amenities_" + amenity_categories[match.code]
            if item not in values[code]:
                values[code].append(item)
        for match in re.finditer(r"(?:^|\n)\s*(?:tower|wing)\s+([A-Za-z0-9-]+)\s*[:=-]\s*([^\n]{1,220})", text, re.I):
            detail = match.group(2)
            units = re.search(r"\b(\d+)\s*(?:residential\s+)?(?:units|flats|apartments)\b", detail, re.I)
            lifts = re.search(r"\b(\d+)\s*(?:lifts|elevators)\b", detail, re.I)
            if units or lifts:
                per_floor = bool(re.search(r"(?:per\s*floor|/\s*floor)", detail, re.I))
                towers.append({"tower": match.group(1), "residentialUnits": int(units.group(1)) if units and not per_floor else None,
                               "unitsPerFloor": int(units.group(1)) if units and per_floor else None,
                               "lifts": int(lifts.group(1)) if lifts else None,
                               "documentId": str(document_id), "pageNumber": page.page_number,
                               "evidenceText": match.group(0).strip(), "validationStatus": "unverified"})
    return values, towers


def station_result(kind, provider_result, origin, claim):
    station = (provider_result.get("data") or {}).get(kind)
    result = {"status": "unavailable", "name": None, "isNearestVerified": False,
              "walkingMinutes": None, "drivingMinutes": None, "walkingDirectionsUri": None,
              "drivingDirectionsUri": None, "documentClaim": claim}
    if provider_result["status"] != "available" or not isinstance(station, dict) or not isinstance(station.get("name"), str):
        return result
    result.update(status="available", name=station["name"][:200],
                  isNearestVerified=station.get("isNearestVerified") is True,
                  walkingMinutes=numeric(station.get("walkingMinutes")), drivingMinutes=numeric(station.get("drivingMinutes")),
                  walkingDirectionsUri=journey_link(origin, station["name"], "walking"),
                  drivingDirectionsUri=journey_link(origin, station["name"], "driving"),
                  sourceUrl=provider_result["sourceUrl"], retrievedAt=provider_result["retrievedAt"])
    return result


def build_project_output(run, pages, image_manifest, visual=None):
    values, tower_details = extract_fields(pages, run.document_id)
    visual = visual or {"status": "not_requested", "claims": [], "pages": []}
    tower_details.extend(visual.get("towers", []))
    for claim in visual["claims"]:
        code, value = claim["fieldCode"], claim["value"]
        if code in {"landHoldingSqFt", "totalTowers", "totalResidentialUnits", "totalParkings", "offeredPriceInr"}:
            value = numeric(value.replace(",", ""))
            if value is None or (code.startswith("total") and not value.is_integer()):
                continue
            value = int(value) if code.startswith("total") else round(value, 2)
        if code == "reraNumbers":
            if not RERA.fullmatch(value.strip()):
                continue
            value = value.strip().upper()
        values[code].append({key: item for key, item in claim.items() if key != "fieldCode"} | {"value": value})
    provider = backend()
    numbers = sorted({item["value"] for item in values["reraNumbers"]})
    registrations = []
    for number in numbers[:20]:
        record = fetch(provider, "registration", number, maharera=True)
        data = record.get("data") or {}
        # Registry evidence must explicitly identify the exact project registration.
        if record["status"] == "available" and data.get("registrationNumber") != number:
            record = {"status": "unavailable", "data": None, "reasonCode": "REGISTRATION_MISMATCH"}
        project_url = data.get("projectUrl")
        registrations.append({"number": number, "projectUrl": project_url if record["status"] == "available" and source_url(project_url, True) else None,
                              "registrySearchUrl": "https://maharera.maharashtra.gov.in/", "verification": record})
    parcel = {code: field(values[code]) for code in ["landHoldingSqFt", "totalTowers", "totalResidentialUnits", "totalParkings"]}
    for code, entry in parcel.items():
        matches = [{"registrationNumber": registration["number"], "value": numeric((registration["verification"].get("data") or {}).get(code)),
                    "sourceUrl": registration["verification"].get("sourceUrl")}
                   for registration in registrations if registration["verification"]["status"] == "available"]
        matches = [item for item in matches if item["value"] is not None]
        entry["registryEvidence"] = matches
        entry["validationStatus"] = "unavailable"
        if matches:
            # Multiple phases/registrations cannot safely be compared to a brochure-wide total.
            entry["validationStatus"] = "scope_review_required" if len(registrations) != 1 else "not_comparable"
            if len(registrations) == 1 and entry["value"] is not None:
                entry["validationStatus"] = "matched" if abs(entry["value"] - matches[0]["value"]) <= (0.1 if code == "landHoldingSqFt" else 0) else "mismatch"
    land, units = parcel["landHoldingSqFt"]["value"], parcel["totalResidentialUnits"]["value"]
    parcel["effectiveParcel"] = {"value": round(land / units, 2) if land is not None and units else None,
                                "unit": "sqft/residential_unit", "formula": "landHoldingSqFt / totalResidentialUnits",
                                "status": "derived_unverified" if land is not None and units else "unknown"}
    parcel["towerUnitsAndLifts"] = tower_details
    location = field(values["location"])
    origin = location["value"]
    stations = fetch(provider, "stations", origin) if origin else {"status": "unavailable", "data": None}
    developer = field(values["developerName"])
    record = fetch(provider, "developer", numbers, maharera=True) if numbers else {"status": "unavailable", "data": None}
    sentiment = fetch(provider, "sentiment", developer["value"]) if developer["value"] else {"status": "unavailable", "data": None}
    return {
        "schemaVersion": VERSION, "projectId": str(run.project_id), "ingestionRunId": str(run.id),
        "documentId": str(run.document_id), "createdAt": timezone.now().isoformat(), "reviewState": "unreviewed",
        "visualAnalysis": {key: value for key, value in visual.items() if key != "claims"},
        "name": field(values["name"]), "submittedName": run.project.submitted_name, "location": location,
        "rera": {"registrations": registrations, "evidence": values["reraNumbers"]}, "parcelDetails": parcel,
        "nearestMetro": station_result("metro", stations, origin, field(values["metroClaim"])),
        "nearestLocalStation": station_result("local", stations, origin, field(values["localClaim"])),
        "images": {category: [item["uri"] for item in image_manifest if item["category"] == category]
                   for category in ["general", "floor-plans", "layouts", "amenities", "unclassified"]},
        "imageEvidence": image_manifest,
        "amenities": {category: {"values": sorted({item["value"] for item in values["amenities_" + category]}),
                                 "evidence": values["amenities_" + category]} for category in AMENITIES},
        "offeredPriceInr": field(values["offeredPriceInr"]), "offers": field(values["offers"]),
        "possession": field(values["possession"]),
        "developer": {"name": developer, "mahareraRecord": record, "socialSentiment": sentiment},
        "limitations": ["Document claims are unreviewed, not registry verification.",
                        "Visual model claims and heuristic page classification require human review.",
                        "Nearest stations and typical journey times need a configured transit provider.",
                        "Social sentiment is a separate third-party signal, not a developer fact."],
    }


def layout_metrics(plan, parcel):
    rooms = []
    for dimension in plan.dimensions:
        if not isinstance(dimension, dict):
            continue
        length, width = numeric(dimension.get("lengthFt")), numeric(dimension.get("widthFt"))
        if length and width:
            rooms.append({"room": str(dimension.get("room", "")), "lengthFt": length, "widthFt": width,
                          "areaSqFt": round(length * width, 2)})
    master = next((room["areaSqFt"] for room in rooms if "master" in room["room"].lower()), None)
    bedroom2 = next((room["areaSqFt"] for room in rooms if re.search(r"bed(?:room)?\s*[- ]?2\b", room["room"], re.I)), None)
    # Neither rectangle sums nor carpet-minus-room-sums establish unused space.
    return {"floorPlanId": str(plan.id), "pageNumber": plan.page_number, "scope": plan.plan_scope,
            "planName": plan.display_title, "displayName": plan.display_name, "displayTitle": plan.display_title,
            "configuration": plan.configuration_type or None, "carpetAreaSqFt": numeric(plan.carpet_area_sqft),
            "rooms": rooms, "roomRectangleSumSqFt": round(sum(room["areaSqFt"] for room in rooms), 2) if rooms else None,
            "Master_Bedroom_sqft_adj": master, "Bedroom2_sqft_adj": bedroom2,
            "PSF": None, "Locn_PSF": None, "Metro_walk_min": None, "Local_walk_min": None,
            "Density": parcel["effectiveParcel"]["value"], "lift": None, "Unused_Space": None,
            "time_dep": None, "Score_Total": None,
            "status": "candidate_metrics" if rooms else "pending_dimensions",
            "reviewState": plan.review_state,
            "provenance": {"extractorVersion": plan.extractor_version, "assetId": str(plan.asset_id) if plan.asset_id else None},
            "warnings": ["Room areas are rectangular approximations; no wall/shape adjustment applied.",
                         "Prices are not linked to individual plans, so PSF is unknown.",
                         "Score weights, time depreciation and unused-space geometry are unspecified."]}


def materialize_project_amenities(project, project_output):
    """Expose recognized extracted amenities without promoting unreviewed free text."""
    from .models import Amenity, ProjectAmenity

    recognized = {code: category for category, terms in AMENITIES.items() for code in terms}
    materialized = []
    for section in project_output.get("amenities", {}).values():
        evidence = section.get("evidence", []) if isinstance(section, dict) else []
        for code in section.get("values", []) if isinstance(section, dict) else []:
            if code not in recognized:
                continue
            amenity, _ = Amenity.objects.get_or_create(
                taxonomy_code=code,
                defaults={"display_name": code.replace("_", " ").title(), "category": recognized[code]},
            )
            claim, _ = ProjectAmenity.objects.get_or_create(project=project, amenity=amenity)
            summary = dict(claim.evidence_summary)
            # Supplemental pages must not erase earlier (possibly reviewed) evidence.
            combined = list(summary.get("extracted", []))
            for item in evidence:
                if item.get("value") == code and item not in combined:
                    combined.append(item)
            summary["extracted"] = combined
            summary["reviewState"] = "unreviewed"
            summary["schemaVersion"] = VERSION
            claim.evidence_summary = summary
            claim.save(update_fields=["evidence_summary", "updated_at"])
            materialized.append(claim)
    return materialized


def store_outputs(run, project_output, plans):
    from .floor_plan_visibility import HIDDEN_REVIEW_STATES

    metrics = {"schemaVersion": "layout-metrics-1.0", "projectId": str(run.project_id), "ingestionRunId": str(run.id),
               "metricUnits": {"PSF": "INR/sqft", "Density": "sqft/residential_unit", "lift": "units/lift"},
               "layouts": [layout_metrics(plan, project_output["parcelDetails"]) for plan in plans
                           if plan.review_state not in HIDDEN_REVIEW_STATES]}
    for item in metrics["layouts"]:
        item["Metro_walk_min"] = project_output["nearestMetro"]["walkingMinutes"]
        item["Local_walk_min"] = project_output["nearestLocalStation"]["walkingMinutes"]
    metrics["towerLiftMetrics"] = [dict(tower, unitsPerLift=round(tower["residentialUnits"] / tower["lifts"], 2)
        if tower["residentialUnits"] is not None and tower["lifts"] else None)
        for tower in project_output["parcelDetails"]["towerUnitsAndLifts"]]
    prefix = f"output/media/projects/{run.project_id}/{run.id}"
    outputs = {}
    for name, data in [("project", project_output), ("layout-metrics", metrics)]:
        key = storage.save(f"{prefix}/{name}.json", ContentFile(json.dumps(data, indent=2, allow_nan=False).encode()))
        outputs[name] = {"objectKey": key, "mediaType": "application/json",
                         "uri": f"/api/v1/ingestion-runs/{run.id}/outputs/{name}"}
    columns = ["floorPlanId", "planName", "displayName", "displayTitle", "pageNumber", "configuration", "carpetAreaSqFt", "Master_Bedroom_sqft_adj",
               "Bedroom2_sqft_adj", "PSF", "Locn_PSF", "Metro_walk_min", "Local_walk_min", "Density", "lift",
               "Unused_Space", "time_dep", "Score_Total", "status"]
    csv_buffer = io.StringIO()
    writer = csv.DictWriter(csv_buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in metrics["layouts"]:
        # Prevent formula injection in spreadsheet exports from document/model text.
        writer.writerow({key: "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value for key, value in row.items()})
    key = storage.save(f"{prefix}/layout-metrics.csv", ContentFile(csv_buffer.getvalue().encode("utf-8-sig")))
    outputs["layout-metrics-csv"] = {"objectKey": key, "mediaType": "text/csv", "uri": f"/api/v1/ingestion-runs/{run.id}/outputs/layout-metrics-csv"}
    return outputs