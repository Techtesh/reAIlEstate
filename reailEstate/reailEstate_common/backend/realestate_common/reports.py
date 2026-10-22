"""Atomic JSON checkpoints shared by maintenance commands."""

import json
import time
from pathlib import Path
from uuid import uuid4


def write_json_report(path, report, *, newline=False, replace_attempts=1, **json_options):
    """Replace checkpoints only after writing succeeds; retry Windows sharing conflicts."""
    if replace_attempts < 1:
        raise ValueError("replace_attempts must be positive")
    path = Path(path)
    content = json.dumps(report, indent=2, **json_options) + ("\n" if newline else "")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        for attempt in range(replace_attempts):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == replace_attempts - 1:
                    raise
                time.sleep(0.1 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)
