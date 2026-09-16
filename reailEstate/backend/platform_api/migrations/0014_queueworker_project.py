from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("platform_api", "0013_queue_worker")]

    operations = [
        migrations.AddField(
            model_name="queueworker",
            name="project",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    to="platform_api.project"),
        ),
    ]