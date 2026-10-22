from django.db import migrations, models
import platform_api.models


class Migration(migrations.Migration):
    dependencies = [("platform_api", "0014_queueworker_project")]

    operations = [
        migrations.AddField(
            model_name="project",
            name="project_pic",
            field=models.ImageField(blank=True, help_text="Main image shown on buyer listing cards.",
                                    upload_to=platform_api.models.project_picture_path, verbose_name="Project pic"),
        ),
    ]
