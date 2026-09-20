#!/usr/bin/env python
import os
import sys
from pathlib import Path


def main():
    project_root = Path(__file__).resolve().parents[1]
    shared_backend = project_root / "reailEstate_common" / "backend"
    sys.path.insert(0, str(shared_backend))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "admin_service.settings")

    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
