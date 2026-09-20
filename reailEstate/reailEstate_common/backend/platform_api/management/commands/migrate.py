from django.conf import settings
from django.core.management.base import CommandError
from django.core.management.commands.migrate import Command as DjangoMigrateCommand


class Command(DjangoMigrateCommand):
    def handle(self, *args, **options):
        if not getattr(settings, "SCHEMA_MIGRATION_OWNER", False):
            raise CommandError("Only the admin service owns shared-database schema migrations.")
        return super().handle(*args, **options)
