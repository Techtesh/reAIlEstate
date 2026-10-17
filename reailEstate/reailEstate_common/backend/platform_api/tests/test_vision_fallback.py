"""Ordered fallback and file configuration, with no model or network calls."""
import json
from unittest.mock import Mock

import pytest

from config.vision_profiles import load_vision_profiles
from platform_api import floor_plan_vision as vision
from platform_api.integrations import InvalidProviderResponse, ProviderUnavailable
from platform_api.vision_errors import AllVisionProvidersFailed, VisionRequestError


@pytest.fixture
def catalog(settings):
    settings.FLOOR_PLAN_EXTRACTION_MODE = "ai"
    settings.FLOOR_PLAN_VISION_PRECHECK_PROFILE = ""
    settings.FLOOR_PLAN_VISION_PROFILES = {
        key: {"provider": "ollama", "model": key, "base_url": f"http://127.0.0.1:{port}/v1"}
        for key, port in [("first", 11434), ("second", 11435), ("third", 11436)]
    }
    return settings.FLOOR_PLAN_VISION_PROFILES


@pytest.fixture
def adapters(monkeypatch):
    called, providers = [], []
    failures = {}
    def factory(selection, profile):
        provider = Mock(code=profile["provider"], model=profile["model"], profile_id=profile["id"])
        def analyze(*args, **kwargs):
            called.append(profile["id"])
            error = failures.get(profile["id"])
            if error:
                raise error
            return {"pageType": "non_plan", "floorPlans": []}
        provider.analyze_layout.side_effect = analyze
        providers.append(provider)
        return provider
    monkeypatch.setattr(vision, "OpenAIFloorPlanVisionProvider", factory)
    return called, providers, failures


@pytest.mark.parametrize("error", [ProviderUnavailable("private auth details"),
    InvalidProviderResponse("private response"),
    *[VisionRequestError(code) for code in ["VISION_TIMEOUT", "VISION_CONNECTION_ERROR",
        "VISION_AUTHENTICATION_OR_ACCESS_DENIED", "VISION_REQUEST_REJECTED",
        "VISION_RATE_LIMITED", "VISION_TRANSIENT_HTTP_ERROR"]]])
def test_provider_failures_advance_and_preserve_winner(catalog, adapters, error):
    called, providers, failures = adapters
    failures["first"] = error
    failures["second"] = InvalidProviderResponse("bad output")
    provider = vision.get_floor_plan_vision_provider()
    result = provider.analyze_layout(b"image", "image/png", {})
    assert result["pageType"] == "non_plan"
    assert called == ["first", "second", "third"]
    assert (provider.profile_id, provider.model) == ("third", "third")
    assert [a["outcome"] for a in provider.vision_attempts] == ["failed", "failed", "succeeded"]
    assert "private" not in json.dumps(vision.provider_metadata(provider))
    for attempted in providers:
        attempted.close.assert_called_once()


def test_manual_first_then_file_order(catalog, adapters):
    called, _, failures = adapters
    failures["second"] = ProviderUnavailable()
    provider = vision.get_floor_plan_vision_provider(vision.resolve_ai_vision_selection("second"))
    provider.analyze_layout(b"image", "image/png", {})
    assert called == ["second", "first"]


def test_failed_precheck_does_not_block_remaining_chain(catalog, adapters, monkeypatch):
    factory = vision.OpenAIFloorPlanVisionProvider
    def with_gate(selection, profile):
        provider = factory(selection, profile)
        provider.analyze_precheck.side_effect = ProviderUnavailable()
        return provider
    monkeypatch.setattr(vision, "OpenAIFloorPlanVisionProvider", with_gate)
    provider = vision.FallbackFloorPlanVisionProvider(vision.resolve_ai_vision_selection(),
        vision._configured_profiles()[0], "third")
    provider.analyze_layout(b"image", "image/png", {})
    assert adapters[0] == ["second"]
    assert provider.vision_attempts[0]["profileId"] == "third"


def test_precheck_rejection_records_actual_winner(catalog, adapters, monkeypatch):
    factory = vision.OpenAIFloorPlanVisionProvider
    def with_gate(selection, profile):
        provider = factory(selection, profile)
        provider.analyze_precheck.return_value = {"pageType": "non_plan", "visualEvidence": "No plan geometry"}
        return provider
    monkeypatch.setattr(vision, "OpenAIFloorPlanVisionProvider", with_gate)
    provider = vision.FallbackFloorPlanVisionProvider(vision.resolve_ai_vision_selection(),
        vision._configured_profiles()[0], "third")
    assert provider.analyze_layout(b"image", "image/png", {})["pageType"] == "non_plan"
    assert provider.profile_id == "third" and len(provider.vision_attempts) == 1
    assert adapters[0] == []


def test_current_catalog_resolves_legacy_pin_and_freezes_job(catalog, adapters):
    pin = vision.resolve_ai_vision_selection("first")
    catalog["first"]["model"] = "replacement"
    provider = vision.get_floor_plan_vision_provider(pin)
    catalog["first"]["model"] = "later-change"
    provider.analyze_layout(b"image", "image/png", {})
    assert provider.model == "replacement"


def test_removed_preference_falls_back_with_safe_record(catalog, adapters):
    pin = vision.resolve_ai_vision_selection("first")
    del catalog["first"]
    provider = vision.get_floor_plan_vision_provider(pin)
    provider.analyze_layout(b"image", "image/png", {})
    assert adapters[0] == ["second"]
    assert provider.vision_attempts[0]["errorCode"] == "VISION_PROVIDER_UNAVAILABLE"


def test_valid_non_plan_does_not_fall_back(catalog, adapters):
    provider = vision.get_floor_plan_vision_provider()
    assert provider.analyze_layout == provider.analyze_layout
    provider.analyze_layout(b"image", "image/png", {})
    assert adapters[0] == ["first"]


@pytest.mark.parametrize("error", [ValueError("invalid input"), RuntimeError("programming error")])
def test_application_errors_do_not_fall_back(catalog, adapters, error):
    adapters[2]["first"] = error
    with pytest.raises(type(error)):
        vision.get_floor_plan_vision_provider().analyze_layout(b"image", "image/png", {})
    assert adapters[0] == ["first"]
    adapters[1][0].close.assert_called_once()


def test_exhausted_chain_records_each_attempt_once(catalog, adapters):
    adapters[2].update({key: ProviderUnavailable("secret") for key in catalog})
    with pytest.raises(AllVisionProvidersFailed) as failure:
        vision.get_floor_plan_vision_provider().analyze_layout(b"image", "image/png", {})
    assert failure.value.code == "VISION_ALL_PROVIDERS_FAILED"
    assert len(failure.value.attempts) == 3
    assert adapters[0] == list(catalog)
    assert "secret" not in repr(failure.value.__dict__)


def test_cancellation_stops_before_next_provider(catalog, adapters, monkeypatch):
    adapters[2]["first"] = ProviderUnavailable()
    monkeypatch.setattr(vision, "_check_job_cancelled", Mock(side_effect=[None, ValueError("EVALUATION_CANCELLED")]))
    with pytest.raises(ValueError, match="EVALUATION_CANCELLED"):
        vision.get_floor_plan_vision_provider().analyze_layout(b"image", "image/png", {})
    assert adapters[0] == ["first"]


def test_timeout_is_frozen_in_job_catalog(catalog):
    catalog["first"]["timeout_seconds"] = 12.5
    provider = vision.get_floor_plan_vision_provider()
    catalog["first"]["timeout_seconds"] = 99
    assert provider._profiles["first"]["timeout_seconds"] == 12.5


def test_actual_sdk_timeout_is_configured(settings, monkeypatch):
    from .test_floor_plan_vision import PNG, SCHEMA, VALID_RESULT
    from contextlib import nullcontext
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    import sys
    settings.FLOOR_PLAN_EXTRACTION_MODE = "ai"
    settings.FLOOR_PLAN_VISION_PRECHECK_PROFILE = ""
    settings.FLOOR_PLAN_VISION_PROFILES = {"local": {
        "provider": "ollama", "model": "local", "timeout_seconds": 12.5}}
    client = MagicMock()
    client.__enter__.return_value = client
    client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content=json.dumps(VALID_RESULT), refusal=None))])
    sdk = Mock(return_value=client)
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=sdk))
    monkeypatch.setattr(vision, "model_request_slot", lambda _: nullcontext())
    vision.get_floor_plan_vision_provider().analyze(PNG, "image/png", SCHEMA)
    assert sdk.call_args.kwargs["timeout"] == 12.5
    assert sdk.call_args.kwargs["max_retries"] == 0


def test_file_locator_is_shared_and_old_environment_is_ignored(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys
    from pathlib import Path
    path = tmp_path / "vision-profiles.json"
    path.write_text(json.dumps({"profiles": [{"id": "file-only", "provider": "ollama", "model": "from-file"}]}))
    environment = {**os.environ, "FLOOR_PLAN_VISION_PROFILES_FILE": str(path),
        "FLOOR_PLAN_VISION_PROFILES": "not json", "FLOOR_PLAN_DEFAULT_VISION_PROFILE": "stale"}
    result = subprocess.run([sys.executable, "-c",
        "from config.settings import FLOOR_PLAN_VISION_PROFILES; print(list(FLOOR_PLAN_VISION_PROFILES))"],
        env=environment, cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "['file-only']"


def test_file_order_and_precheck(tmp_path):
    path = tmp_path / "vision-profiles.json"
    path.write_text(json.dumps({"profiles": [{"id": "b", "model": "b"}, {"id": "a", "model": "a"}],
                               "precheck_profile_id": "a"}))
    profiles, precheck = load_vision_profiles(path)
    assert list(profiles) == ["b", "a"] and precheck == "a"
    assert load_vision_profiles(tmp_path / "missing.json") == ({}, "")


@pytest.mark.parametrize("data", [[], {}, {"profiles": {}}, {"profiles": [None]},
    {"profiles": [{"id": "a"}, {"id": "a"}]}, {"profiles": [{"id": "a", "enabled": True}]},
    {"profiles": [{"id": "a", "api_key": "secret"}]},
    {"profiles": [], "precheck_profile_id": "missing"}])
def test_bad_files_fail_safely(tmp_path, data):
    path = tmp_path / "vision-profiles.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Invalid vision-profiles.json configuration"):
        load_vision_profiles(path)


@pytest.mark.parametrize("timeout", [0, -1, True, "10", float("nan"), float("inf")])
def test_invalid_timeout_rejected(catalog, timeout):
    catalog["first"]["timeout_seconds"] = timeout
    with pytest.raises(ValueError):
        vision._configured_profiles()
