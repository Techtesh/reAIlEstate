"""Load the administrator-owned, ordered vision catalog; never load credentials."""

import json
from pathlib import Path


def load_vision_profiles(path):
    path = Path(path)
    if not path.exists():
        return {}, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or set(data) - {"profiles", "precheck_profile_id"}:
            raise ValueError
        entries = data["profiles"]
        if not isinstance(entries, list):
            raise ValueError
        profiles = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError
            profile = dict(entry)
            identifier = profile.pop("id")
            if (not isinstance(identifier, str) or not identifier.strip()
                    or identifier != identifier.strip() or identifier in profiles):
                raise ValueError
            if set(profile) - {"label", "provider", "model", "base_url", "response_format", "api_key_env", "timeout_seconds"}:
                raise ValueError
            profiles[identifier] = profile
        precheck = data.get("precheck_profile_id", "")
        if not isinstance(precheck, str) or (precheck and precheck not in profiles):
            raise ValueError
        return profiles, precheck
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("Invalid vision-profiles.json configuration.") from None
