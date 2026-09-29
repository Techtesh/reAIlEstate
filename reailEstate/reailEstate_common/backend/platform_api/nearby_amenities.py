"""Durable enrichment of evidence-backed nearby amenity claims."""

from uuid import uuid4

from .project_enrichment import backend, fetch, source_url


NEARBY_AMENITIES_EVENT = "project.nearby_amenities.requested"
MAX_NEARBY_AMENITY_DISTANCE_METERS = 5_000


def enqueue_nearby_amenity_enrichment(project, claims):
    """Queue one follow-up only when a completed extraction found amenities."""
    if not claims:
        return None
    from .models import OutboxMessage
    pending = OutboxMessage.objects.filter(event_type=NEARBY_AMENITIES_EVENT, payload__projectId=str(project.pk), published_at__isnull=True, dead_lettered_at__isnull=True).order_by("occurred_at").first()
    if pending:
        return pending
    return OutboxMessage.objects.create(event_type=NEARBY_AMENITIES_EVENT, schema_version="1.0", payload={"projectId": str(project.pk)}, correlation_id=uuid4())


def enrich_nearby_amenities(project_id):
    """Materialize provider results within the product's fixed 5 km scope."""
    from .models import Amenity, Project, ProjectAmenity
    from .project_outputs import AMENITIES, VERSION
    project = Project.objects.get(pk=project_id)
    if project.status in {"retired", "merged"} or project.latitude is None or project.longitude is None:
        return {"status": "skipped", "claimCount": 0}
    result = fetch(backend(), "nearby_amenities", {"latitude": float(project.latitude), "longitude": float(project.longitude), "radiusMeters": MAX_NEARBY_AMENITY_DISTANCE_METERS})
    if result["status"] != "available":
        return {"status": "unavailable", "claimCount": 0}
    recognized = {code: category for category, terms in AMENITIES.items() for code in terms}
    rows = result["data"].get("amenities")
    if not isinstance(rows, list):
        return {"status": "invalid", "claimCount": 0}
    count = 0
    for row in rows[:100]:
        if not isinstance(row, dict):
            continue
        code, distance, minutes = row.get("taxonomyCode"), row.get("distanceMeters"), row.get("travelMinutes")
        if (code not in recognized or type(distance) is not int or not 0 <= distance <= MAX_NEARBY_AMENITY_DISTANCE_METERS or (minutes is not None and (type(minutes) is not int or minutes < 0))):
            continue
        amenity, _ = Amenity.objects.get_or_create(taxonomy_code=code, defaults={"display_name": code.replace("_", " ").title(), "category": recognized[code]})
        claim, created = ProjectAmenity.objects.get_or_create(project=project, amenity=amenity)
        # An in-society claim is stronger and must never be replaced by a nearby substitute.
        if not created and claim.distance_meters is None:
            continue
        summary = dict(claim.evidence_summary)
        evidence = {"sourceUrl": result["sourceUrl"], "retrievedAt": result["retrievedAt"], "distanceMeters": distance, "reviewState": "unreviewed"}
        if isinstance(row.get("evidenceUrl"), str) and source_url(row["evidenceUrl"]):
            evidence["evidenceUrl"] = row["evidenceUrl"]
        summary["nearby"] = [evidence]
        summary["reviewState"] = "unreviewed"
        summary["schemaVersion"] = VERSION
        claim.distance_meters, claim.travel_minutes, claim.availability_state, claim.evidence_summary = distance, minutes, "available", summary
        claim.save()
        count += 1
    return {"status": "completed", "claimCount": count}