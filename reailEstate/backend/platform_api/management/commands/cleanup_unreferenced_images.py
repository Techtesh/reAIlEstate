import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from platform_api.artifact_storage import storage
from platform_api.models import FloorPlanAsset


class Command(BaseCommand):
    help = "Preview or delete generated image files not referenced by any FloorPlanAsset."

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true", help="Delete the listed unreferenced files.")

    def handle(self, *args, **options):
        if options["execute"] and (
            not settings.DEBUG or str(getattr(settings, "ENVIRONMENT", "")).casefold() not in {"local", "development"}
        ):
            raise CommandError("Image cleanup execution is restricted to local/development environments.")
        root = Path(settings.WORKSPACE_DATA_ROOT) / "output" / "media" / "images"
        referenced = {str(key).replace("\\", "/") for key in FloorPlanAsset.objects.values_list("object_key", flat=True)}
        orphaned = []
        if root.exists():
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                key = path.relative_to(settings.WORKSPACE_DATA_ROOT).as_posix()
                if key not in referenced:
                    orphaned.append({"key": key, "bytes": path.stat().st_size})
        if options["execute"]:
            for item in orphaned:
                storage.delete(item["key"])
        result = {
            "mode": "execute" if options["execute"] else "preview",
            "unreferencedCount": len(orphaned),
            "unreferencedBytes": sum(item["bytes"] for item in orphaned),
            "deleted": len(orphaned) if options["execute"] else 0,
            "files": orphaned,
        }
        self.stdout.write(json.dumps(result, indent=2))