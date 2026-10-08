from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("platform_api", "0020_reusable_organization_roles")]

    operations = [
        migrations.AlterField(
            model_name="organizationrole", name="base_role",
            field=models.CharField(max_length=32, choices=[
                ("owner", "Owner"), ("admin", "Administrator"),
                ("editor", "Editor"), ("viewer", "Viewer"),
            ]),
        ),
        migrations.AlterField(
            model_name="organizationmembership", name="role",
            field=models.CharField(max_length=32, choices=[
                ("owner", "Owner"), ("admin", "Administrator"),
                ("editor", "Editor"), ("viewer", "Viewer"),
            ]),
        ),
    ]
