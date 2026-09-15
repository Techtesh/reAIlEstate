import uuid

from django.db import migrations
from django.utils import timezone


def import_legacy_merges(apps, schema_editor):
    Project = apps.get_model("platform_api", "Project")
    ProjectMerge = apps.get_model("platform_api", "ProjectMerge")
    for source in Project.objects.filter(address__has_key="mergedIntoProjectId").iterator():
        try:
            target_id = uuid.UUID(str(source.address.get("mergedIntoProjectId")))
        except (TypeError, ValueError):
            continue
        target = Project.objects.filter(id=target_id, organization_id=source.organization_id).first()
        if target is None or target.id == source.id:
            continue
        ProjectMerge.objects.update_or_create(
            source_project_id=source.id,
            defaults={
                "target_project_id": target.id,
                "state": "applied",
                "reason_code": str(source.address.get("mergeReason") or "LEGACY_PROJECT_ALIAS")[:64],
                "notes": "Imported from legacy project address metadata.",
                "applied_at": timezone.now(),
            },
        )


class Migration(migrations.Migration):
    dependencies = [("platform_api", "0007_layout_pages_project_merges_and_queue_indexes")]
    operations = [migrations.RunPython(import_legacy_merges, migrations.RunPython.noop)]
