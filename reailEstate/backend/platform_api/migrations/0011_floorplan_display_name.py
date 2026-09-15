from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("platform_api", "0010_layoutcropdraft_source_page")]

    operations = [
        migrations.AddField(
            model_name="floorplan",
            name="display_name",
            field=models.CharField(blank=True, max_length=160),
        ),
    ]