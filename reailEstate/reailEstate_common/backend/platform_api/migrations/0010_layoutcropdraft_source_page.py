import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("platform_api", "0009_layoutcropdraft")]

    operations = [
        migrations.AlterField(
            model_name="layoutcropdraft", name="source_plan",
            field=models.ForeignKey(to="platform_api.floorplan", null=True, blank=True,
                                    on_delete=django.db.models.deletion.CASCADE, related_name="crop_drafts"),
        ),
        migrations.AddField(
            model_name="layoutcropdraft", name="document",
            field=models.ForeignKey(to="platform_api.evidencedocument", null=True, blank=True,
                                    on_delete=django.db.models.deletion.CASCADE, related_name="layout_crop_drafts"),
        ),
        migrations.AddField(
            model_name="layoutcropdraft", name="page_number",
            field=models.PositiveIntegerField(null=True, blank=True),
        ),
        migrations.AddConstraint(
            model_name="layoutcropdraft",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(source_plan__isnull=False, document__isnull=True, page_number__isnull=True)
                    | models.Q(source_plan__isnull=True, document__isnull=False,
                               page_number__isnull=False, page_number__gte=1)
                ),
                name="crop_draft_valid_source",
            ),
        ),
    ]