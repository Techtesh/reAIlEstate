from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("platform_api", "0005_floorplan_features_floorplan_plan_scope_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="project",
            name="maha_rera_id",
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
    ]