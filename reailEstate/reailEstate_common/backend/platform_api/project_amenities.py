"""Manual amenity proposals for the shared project editor."""
from uuid import uuid4
from .amenity_matching import find_amenities
from .models import Amenity, AuditEvent, ProjectAmenity
from .project_outputs import AMENITIES


def add_manual_amenities(project, actor, text):
    """Record manual claims as proposals; publication still requires review."""
    text = text.strip()
    if not text:
        return []
    categories = {code: category for category, codes in AMENITIES.items() for code in codes}
    matches = {match.code for match in find_amenities(text) if match.code in categories}
    if not matches:
        return []
    for code in sorted(matches):
        amenity, _ = Amenity.objects.get_or_create(taxonomy_code=code, defaults={
            "display_name": code.replace("_", " ").title(), "category": categories[code],
        })
        claim, _ = ProjectAmenity.objects.get_or_create(project=project, amenity=amenity)
        summary = dict(claim.evidence_summary)
        manual = list(summary.get("manual", []))
        entry = {"text": text, "source": "project_workspace", "reviewState": "unreviewed", "submittedBy": actor.pk}
        if entry not in manual:
            manual.append(entry)
        summary["manual"] = manual
        summary["reviewState"] = "unreviewed"
        claim.evidence_summary = summary
        claim.save(update_fields=["evidence_summary", "updated_at"])
    AuditEvent.objects.create(
        actor=actor, action="project.amenities_added_manually", entity_type="Project",
        entity_id=project.pk, correlation_id=uuid4(), metadata={"amenities": sorted(matches)},
    )
    return sorted(matches)
