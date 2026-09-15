import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("platform_api", "0008_import_legacy_project_merges"),
    ]
    operations = [migrations.CreateModel(
        name="LayoutCropDraft",
        fields=[
            ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("source_sha256", models.CharField(max_length=64)),
            ("label", models.CharField(max_length=64)),
            ("image", models.BinaryField()),
            ("sha256", models.CharField(max_length=64)),
            ("pixel_width", models.PositiveIntegerField()),
            ("pixel_height", models.PositiveIntegerField()),
            ("bounds", models.JSONField(default=dict)),
            ("owner", models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.CASCADE, related_name="layout_crop_drafts")),
            ("project", models.ForeignKey(to="platform_api.project", on_delete=django.db.models.deletion.CASCADE, related_name="layout_crop_drafts")),
            ("source_plan", models.ForeignKey(to="platform_api.floorplan", on_delete=django.db.models.deletion.CASCADE, related_name="crop_drafts")),
        ],
        options={"ordering": ["created_at", "id"], "indexes": [models.Index(fields=["owner", "project"], name="crop_draft_owner_project_idx")]},
    )]