from django.core.management.base import BaseCommand, CommandError

from platform_api.feature_flags.management import FeatureFlagConflict, FeatureFlagUnavailable, sync_defaults
from platform_api.feature_flags.registry import REGISTRY
from platform_api.feature_flags.validation import FlagValidationError, active_environment


class Command(BaseCommand):
    help = "Preview missing registered feature flags in settings.ENVIRONMENT; --apply creates them with audit."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Create missing defaults (never overwrite existing rows).")

    def handle(self, *args, **options):
        try:
            environment = active_environment()
            missing = sync_defaults(apply=options["apply"])
        except (FlagValidationError, FeatureFlagConflict, FeatureFlagUnavailable) as exc:
            raise CommandError(exc.code) from None
        verb = "Created" if options["apply"] else "Would create"
        self.stdout.write(f"{verb} {len(missing)} flag(s) in {environment}.")
        for key in missing:
            self.stdout.write(f"{key}: enabled={REGISTRY[key].default_enabled}")
        if not options["apply"]:
            self.stdout.write("Preview only; pass --apply to create missing rows and audit events.")