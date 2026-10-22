from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("platform_api", "0021_organization_admin_role")]

    operations = [
        migrations.AlterField(model_name="recommendationsnapshot", name="algorithm_version", field=models.CharField(default="match-2.0", max_length=32)),
        migrations.AddField(model_name="recommendationsnapshot", name="matching_context", field=models.JSONField(default=dict)),
        migrations.AddField(model_name="recommendationitem", name="layout_match", field=models.JSONField(default=dict)),
    ]
