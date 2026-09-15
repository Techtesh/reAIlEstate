import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("platform_api", "0006_project_optional_maha_rera_id"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="evidencedocument",
            index=models.Index(fields=["status", "error_category"], name="document_retry_status_idx"),
        ),
        migrations.AddIndex(
            model_name="ingestionrun",
            index=models.Index(fields=["document", "status"], name="ingestion_document_status_idx"),
        ),
        migrations.CreateModel(
            name="DocumentLayoutPage",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("page_number", models.PositiveIntegerField()),
                ("state", models.CharField(choices=[("layout", "Process as layout"), ("excluded", "Do not process as layout")], default="layout", max_length=24)),
                ("source", models.CharField(choices=[("admin", "Administrator"), ("user_flag", "User flag"), ("system", "System")], default="admin", max_length=24)),
                ("reason_code", models.CharField(blank=True, max_length=64)),
                ("notes", models.CharField(blank=True, max_length=1000)),
                ("document", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="layout_page_decisions", to="platform_api.evidencedocument")),
                ("marked_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="layout_page_decisions", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["page_number"],
                "indexes": [models.Index(fields=["document", "state"], name="layout_page_state_idx")],
                "constraints": [models.UniqueConstraint(fields=("document", "page_number"), name="unique_document_layout_page")],
            },
        ),
        migrations.CreateModel(
            name="ProjectMerge",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("state", models.CharField(choices=[("proposed", "Proposed"), ("applied", "Applied"), ("reverted", "Reverted")], default="applied", max_length=16)),
                ("reason_code", models.CharField(max_length=64)),
                ("notes", models.CharField(blank=True, max_length=1000)),
                ("applied_at", models.DateTimeField(blank=True, null=True)),
                ("merged_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="project_merges", to=settings.AUTH_USER_MODEL)),
                ("source_project", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="merge_out", to="platform_api.project")),
                ("target_project", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="merge_sources", to="platform_api.project")),
            ],
            options={
                "indexes": [models.Index(fields=["target_project", "state"], name="project_merge_target_idx")],
                "constraints": [models.CheckConstraint(condition=~models.Q(source_project=models.F("target_project")), name="project_merge_source_differs")],
            },
        ),
    ]