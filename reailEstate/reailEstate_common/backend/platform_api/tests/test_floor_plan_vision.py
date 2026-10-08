import base64
import json
import sys
from copy import deepcopy
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, Mock, patch

import pytest

from platform_api import floor_plan_vision as vision
from platform_api.integrations import InvalidProviderResponse, ProviderUnavailable


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII="
)
SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["floorPlans"],
    "additionalProperties": False,
    "properties": {"floorPlans": {"type": "array", "items": {"$ref": "#/$defs/plan"}}},
    "$defs": {"plan": {
        "type": "object", "required": ["confidence"], "additionalProperties": False,
        "properties": {"confidence": {"type": "number", "minimum": 0, "maximum": 1}},
    }},
}
VALID_RESULT = {"floorPlans": [{"confidence": 0.8}]}


@pytest.fixture(autouse=True)
def configuration(settings, monkeypatch):
    settings.FLOOR_PLAN_EXTRACTION_MODE = "ai"
    settings.FLOOR_PLAN_VISION_PROFILES = {}
    settings.FLOOR_PLAN_DEFAULT_VISION_PROFILE = ""
    settings.FLOOR_PLAN_VISION_PROVIDER = "unavailable"
    settings.FLOOR_PLAN_VISION_BASE_URL = ""
    settings.FLOOR_PLAN_VISION_MODEL = ""
    settings.FLOOR_PLAN_VISION_API_KEY = ""
    monkeypatch.delenv("FLOOR_PLAN_VISION_API_KEY", raising=False)
    monkeypatch.delenv("TEST_VISION_KEY", raising=False)


@pytest.fixture
def profiles(settings):
    settings.FLOOR_PLAN_VISION_PROFILES = {
        "desktop": {"label": "Desktop", "provider": "ollama", "model": "local-vision"},
        "cloud": {
            "label": "Hosted", "provider": "cloud_openai", "model": "cloud-vision",
            "base_url": "https://vision.example.com/v1", "api_key_env": "TEST_VISION_KEY",
        },
    }
    settings.FLOOR_PLAN_DEFAULT_VISION_PROFILE = "desktop"
    return settings.FLOOR_PLAN_VISION_PROFILES


@pytest.fixture
def sdk(monkeypatch):
    """Replace every SDK/transport/auth constructor; these tests make no network calls."""
    response = SimpleNamespace(choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content=json.dumps(VALID_RESULT), refusal=None),
    )])
    client = MagicMock()
    client.__enter__.return_value = client
    client.chat.completions.create.return_value = response
    transport = MagicMock()
    transport.__enter__.return_value = transport
    credential = MagicMock()
    credential.get_token.return_value = SimpleNamespace(token="test-entra-token", expires_on=vision.time.time() + 3600)
    openai = Mock(return_value=client)
    httpx = Mock(return_value=transport)
    identity = Mock(return_value=credential)
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=openai))
    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(Client=httpx))
    monkeypatch.setitem(sys.modules, "azure.identity", SimpleNamespace(DefaultAzureCredential=identity))
    monkeypatch.setattr(vision, "model_request_slot", lambda _: nullcontext())
    return SimpleNamespace(
        openai=openai, httpx=httpx, identity=identity, client=client,
        transport=transport, credential=credential, response=response,
    )


def test_listing_is_private_and_does_not_read_environment(profiles):
    profiles["disabled"] = {"enabled": False, "api_key": "do-not-expose"}
    profiles["empty"] = {"provider": "local_openai", "model": ""}
    with patch.object(vision.os, "environ") as environment:
        environment.get.side_effect = AssertionError("Secrets must not be read while selecting")
        public = vision.list_floor_plan_vision_profiles()
        selection = vision.resolve_vision_selection("cloud")
        vision.get_floor_plan_vision_provider(selection)
        environment.get.assert_not_called()
    assert public == {
        "defaultProfileId": "desktop",
        "profiles": [
            {"id": "desktop", "label": "Desktop", "provider": "ollama", "model": "local-vision", "hosting": "local"},
            {"id": "cloud", "label": "Hosted", "provider": "cloud_openai", "model": "cloud-vision", "hosting": "cloud"},
        ],
    }
    assert set(selection) == {"profileId", "provider", "model", "configurationHash"}
    serialized = json.dumps([public, selection])
    for private in ("vision.example.com", "TEST_VISION_KEY", "base_url", "api_key", "do-not-expose"):
        assert private not in serialized


def test_snapshot_pins_model_when_default_changes(settings, profiles, sdk):
    selected = vision.resolve_vision_selection()
    settings.FLOOR_PLAN_DEFAULT_VISION_PROFILE = "cloud"
    provider = vision.get_floor_plan_vision_provider(selected)
    assert (provider.profile_id, provider.code, provider.model) == ("desktop", "ollama", "local-vision")
    provider.analyze(PNG, "image/png", SCHEMA)
    assert sdk.client.chat.completions.create.call_args.kwargs["model"] == "local-vision"


@pytest.mark.parametrize("change", [
    {"model": "changed-model"}, {"provider": "local_openai"},
    {"base_url": "http://127.0.0.1:11435/v1"}, {"api_key_env": "OTHER_KEY"},
    {"response_format": "json_schema"}, {"enabled": False}, {"model": ""},
])
def test_snapshot_rejects_configuration_drift(profiles, sdk, change):
    selection = vision.resolve_vision_selection()
    provider = vision.get_floor_plan_vision_provider(selection)
    profiles["desktop"].update(change)
    with pytest.raises(ProviderUnavailable):
        vision.get_floor_plan_vision_provider(selection)
    with pytest.raises(ProviderUnavailable):
        provider.analyze(PNG, "image/png", SCHEMA)
    sdk.openai.assert_not_called()


def test_snapshot_rejects_removed_profile(profiles):
    selection = vision.resolve_vision_selection()
    del profiles["desktop"]
    with pytest.raises(ProviderUnavailable):
        vision.get_floor_plan_vision_provider(selection)


def test_label_and_secret_rotation_do_not_change_hash(profiles, monkeypatch, sdk):
    monkeypatch.setenv("TEST_VISION_KEY", "first-secret")
    selection = vision.resolve_vision_selection("cloud")
    provider = vision.get_floor_plan_vision_provider(selection)
    monkeypatch.setenv("TEST_VISION_KEY", "rotated-secret")
    profiles["cloud"]["label"] = "New display label"
    assert selection == vision.resolve_vision_selection("cloud")
    assert "rotated-secret" not in json.dumps(provider.__dict__)
    provider.analyze(PNG, "image/png", SCHEMA)
    assert sdk.openai.call_args.kwargs["api_key"] == "rotated-secret"
    assert "rotated-secret" not in json.dumps(sdk.client.chat.completions.create.call_args.kwargs)
    assert "rotated-secret" not in json.dumps(provider.__dict__)


@pytest.mark.parametrize("profile_id", ["", " ", " desktop", "missing", False, 42, [], {}])
def test_explicit_ids_never_silently_fall_back(profiles, profile_id):
    with pytest.raises(ValueError):
        vision.resolve_vision_selection(profile_id)


def test_disabled_profile_is_not_selectable(profiles):
    profiles["desktop"]["enabled"] = False
    assert vision.list_floor_plan_vision_profiles()["defaultProfileId"] is None
    with pytest.raises(ValueError):
        vision.resolve_vision_selection("desktop")
    with pytest.raises(ProviderUnavailable):
        vision.get_floor_plan_vision_provider()


@pytest.mark.parametrize("default_id", ["", "missing"])
def test_registry_without_valid_default_fails_closed(settings, profiles, default_id):
    settings.FLOOR_PLAN_DEFAULT_VISION_PROFILE = default_id
    settings.FLOOR_PLAN_VISION_PROVIDER = "local_openai"
    settings.FLOOR_PLAN_VISION_MODEL = "legacy-must-not-win"
    assert vision.list_floor_plan_vision_profiles()["defaultProfileId"] is None
    with pytest.raises(ValueError):
        vision.resolve_vision_selection()
    with pytest.raises(ProviderUnavailable):
        vision.get_floor_plan_vision_provider()
    assert vision.resolve_vision_selection("desktop")["model"] == "local-vision"


def test_unconfigured_snapshot_stays_unavailable(settings, sdk):
    assert vision.list_floor_plan_vision_profiles() == {"defaultProfileId": None, "profiles": []}
    selection = vision.resolve_vision_selection()
    assert selection["profileId"] is None
    assert selection["provider"] == "unavailable"
    assert selection["model"] == ""
    assert len(selection["configurationHash"]) == 64
    settings.FLOOR_PLAN_VISION_PROFILES = {"new": {"provider": "ollama", "model": "new-model"}}
    settings.FLOOR_PLAN_DEFAULT_VISION_PROFILE = "new"
    provider = vision.get_floor_plan_vision_provider(selection)
    assert (provider.code, provider.model, provider.profile_id) == ("unavailable", "", None)
    with pytest.raises(ProviderUnavailable):
        provider.analyze(PNG, "image/png", SCHEMA)
    sdk.openai.assert_not_called()
    sdk.identity.assert_not_called()


@pytest.mark.parametrize("selection", [{}, "desktop", [], {"profileId": "desktop"}])
def test_factory_rejects_malformed_snapshot(profiles, selection):
    with pytest.raises(ProviderUnavailable):
        vision.get_floor_plan_vision_provider(selection)


@pytest.mark.parametrize("change", [
    {"model": "client-model"}, {"provider": "cloud_openai"}, {"configurationHash": "forged"},
    {"profileId": "missing"}, {"base_url": "https://attacker.example/v1"},
])
def test_factory_rejects_client_overrides(profiles, change):
    selection = vision.resolve_vision_selection()
    selection.update(change)
    with pytest.raises(ProviderUnavailable):
        vision.get_floor_plan_vision_provider(selection)


@pytest.mark.parametrize("provider,base_url", [
    ("ollama", "http://example.com/v1"), ("local_openai", "http://127.0.0.1.evil.example/v1"),
    ("ollama", "file:///tmp/model"), ("ollama", "http://user:secret@localhost/v1"),
    ("ollama", "http://localhost/v1?key=secret"), ("ollama", "http://localhost/v1?"),
    ("ollama", "http://localhost/v1#fragment"), ("ollama", "http://localhost:99999/v1"),
    ("ollama", "http://localhost:bad/v1"), ("ollama", "http://localhost:0/v1"),
    ("ollama", "http://localhost\\evil/v1"), ("ollama", " http://localhost/v1"),
    ("cloud_openai", "http://vision.example.com/v1"), ("cloud_openai", "https://vision.example.com:8443/v1"),
    ("cloud_openai", "https://127.0.0.1/v1"), ("cloud_openai", "https://localhost/v1"),
    ("cloud_openai", "https://10.0.0.1/v1"), ("cloud_openai", "https://vision.example.com/v1?secret=value"),
    ("azure_foundry", "https://user:secret@vision.example.com/v1"),
    ("azure_foundry", "https://vision.example.com/v1#fragment"),
])
def test_unsafe_endpoints_raise_safe_errors(profiles, provider, base_url):
    profiles["desktop"].update(provider=provider, base_url=base_url, api_key_env="TEST_VISION_KEY")
    for operation in (vision.list_floor_plan_vision_profiles, vision.resolve_vision_selection):
        with pytest.raises(ValueError, match="^Invalid floor-plan vision configuration\\.$") as error:
            operation()
        assert base_url not in str(error.value)
    with pytest.raises(ProviderUnavailable):
        vision.get_floor_plan_vision_provider()


@pytest.mark.parametrize("base_url", ["http://localhost:11434/v1", "https://127.0.0.1/v1", "http://[::1]:11434/v1"])
def test_loopback_urls_are_accepted(profiles, base_url):
    profiles["desktop"]["base_url"] = base_url
    assert vision.resolve_vision_selection()["provider"] == "ollama"


@pytest.mark.parametrize("change", [
    {"provider": "unknown"}, {"provider": []}, {"model": 1}, {"model": " padded "},
    {"enabled": "false"}, {"api_key_env": "literal-secret!"}, {"api_key_env": ""},
    {"response_format": "text"}, {"response_format": []}, {"base_url": None},
])
def test_invalid_configuration_is_value_error(profiles, change):
    profiles["desktop"].update(change)
    with pytest.raises(ValueError, match="^Invalid floor-plan vision configuration\\.$"):
        vision.list_floor_plan_vision_profiles()


@pytest.mark.parametrize("registry", [None, [], "invalid", {"": {}}, {1: {}}, {"bad": None}])
def test_invalid_registries_are_value_errors(settings, registry):
    settings.FLOOR_PLAN_VISION_PROFILES = registry
    with pytest.raises(ValueError):
        vision.resolve_vision_selection()
    with pytest.raises(ProviderUnavailable):
        vision.get_floor_plan_vision_provider()


@pytest.mark.parametrize("provider", ["ollama", "local_openai"])
def test_local_request_has_png_high_detail_and_explicit_schema(profiles, sdk, provider):
    profiles["desktop"]["provider"] = provider
    adapter = vision.get_floor_plan_vision_provider()
    assert adapter.analyze(PNG, "image/png", SCHEMA) == VALID_RESULT
    request = sdk.client.chat.completions.create.call_args.kwargs
    assert request["model"] == "local-vision"
    assert request["response_format"] == {"type": "json_object"}
    assert json.loads(request["messages"][0]["content"].split("JSON Schema:\n", 1)[1]) == SCHEMA
    assert request["messages"][1]["content"][1] == {
        "type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(PNG).decode("ascii"), "detail": "high",
        },
    }
    assert sdk.openai.call_args.kwargs["base_url"] == "http://127.0.0.1:11434/v1/"
    assert sdk.openai.call_args.kwargs["api_key"] == "local-not-secret"
    assert sdk.openai.call_args.kwargs["http_client"] is sdk.transport
    sdk.httpx.assert_called_once_with(follow_redirects=False, trust_env=False,
                                      event_hooks={"request": [ANY], "response": [ANY]})
    assert all(callable(hook) for hooks in sdk.httpx.call_args.kwargs["event_hooks"].values() for hook in hooks)
    sdk.client.__exit__.assert_called_once()
    sdk.transport.__exit__.assert_called_once()
    sdk.identity.assert_not_called()


def test_cloud_uses_key_and_json_schema_never_entra(profiles, sdk, monkeypatch):
    monkeypatch.setenv("TEST_VISION_KEY", "cloud-secret")
    provider = vision.get_floor_plan_vision_provider(vision.resolve_vision_selection("cloud"))
    assert provider.analyze(PNG, "image/png", SCHEMA) == VALID_RESULT
    assert sdk.openai.call_args.kwargs["api_key"] == "cloud-secret"
    assert sdk.client.chat.completions.create.call_args.kwargs["response_format"] == {
        "type": "json_schema", "json_schema": {"name": "floor_plan_extraction", "strict": True, "schema": SCHEMA},
    }
    sdk.identity.assert_not_called()


def test_cloud_requires_auth_reference(profiles):
    del profiles["cloud"]["api_key_env"]
    with pytest.raises(ValueError):
        vision.resolve_vision_selection("cloud")


@pytest.mark.parametrize("provider", ["cloud_openai", "azure_foundry", "local_openai"])
def test_missing_explicit_key_never_falls_back(profiles, sdk, provider):
    profiles["cloud"]["provider"] = provider
    if provider == "local_openai":
        profiles["cloud"]["base_url"] = "http://localhost/v1"
    adapter = vision.get_floor_plan_vision_provider(vision.resolve_vision_selection("cloud"))
    with pytest.raises(ProviderUnavailable):
        adapter.analyze(PNG, "image/png", SCHEMA)
    sdk.identity.assert_not_called()
    sdk.openai.assert_not_called()


def test_azure_entra_credential_is_reused_until_closed(profiles, sdk):
    profiles["cloud"]["provider"] = "azure_foundry"
    del profiles["cloud"]["api_key_env"]
    adapter = vision.get_floor_plan_vision_provider(vision.resolve_vision_selection("cloud"))
    try:
        adapter.analyze(PNG, "image/png", SCHEMA)
        adapter.analyze(PNG, "image/png", SCHEMA)
        sdk.credential.close.assert_not_called()
    finally:
        adapter.close()
    adapter.close()
    sdk.identity.assert_called_once_with()
    sdk.credential.get_token.assert_called_once_with("https://cognitiveservices.azure.com/.default")
    sdk.credential.close.assert_called_once()
    assert sdk.openai.call_args.kwargs["api_key"] == "test-entra-token"


def test_azure_token_failure_is_safe_and_closes_credential(profiles, sdk, monkeypatch):
    profiles["cloud"]["provider"] = "azure_foundry"
    del profiles["cloud"]["api_key_env"]
    sdk.credential.get_token.side_effect = RuntimeError("secret from identity provider")
    monkeypatch.setattr(vision.time, "sleep", Mock())
    adapter = vision.get_floor_plan_vision_provider(vision.resolve_vision_selection("cloud"))
    try:
        with pytest.raises(ProviderUnavailable) as error:
            adapter.analyze(PNG, "image/png", SCHEMA)
    finally:
        adapter.close()
    assert "secret" not in str(error.value)
    assert sdk.credential.get_token.call_count == 3
    sdk.credential.close.assert_called_once()
    sdk.openai.assert_not_called()


@pytest.mark.parametrize("fails", [False, True])
def test_flat_separation_skill_closes_owned_provider(profiles, monkeypatch, fails):
    from platform_api import floor_plan_ai_skill as skill

    provider = Mock()
    provider.analyze_full_floor.return_value = ([], {"unitCount": 0})
    if fails:
        provider.analyze_full_floor.side_effect = ProviderUnavailable("Unavailable")
    monkeypatch.setattr(skill, "get_floor_plan_vision_provider", Mock(return_value=provider))
    if fails:
        with pytest.raises(ProviderUnavailable):
            skill.FloorPlanFlatSeparationAISkill.invoke(PNG, "image/png")
    else:
        regions, metadata = skill.FloorPlanFlatSeparationAISkill.invoke(PNG, "image/png")
        assert regions == [] and metadata["unitCount"] == 0
        assert metadata["skill"] == skill.FloorPlanFlatSeparationAISkill.name
    provider.close.assert_called_once_with()


def test_legacy_key_is_read_only_at_execution(settings, sdk):
    settings.FLOOR_PLAN_VISION_PROVIDER = "azure_foundry"
    settings.FLOOR_PLAN_VISION_MODEL = "legacy-model"
    settings.FLOOR_PLAN_VISION_BASE_URL = "https://vision.example.com/openai/v1"
    settings.FLOOR_PLAN_VISION_API_KEY = "old-key"
    selection = vision.resolve_vision_selection()
    assert selection["profileId"] == "default"
    provider = vision.get_floor_plan_vision_provider(selection)
    settings.FLOOR_PLAN_VISION_API_KEY = "new-key"
    assert selection == vision.resolve_vision_selection()
    assert "old-key" not in json.dumps(provider.__dict__)
    provider.analyze(PNG, "image/png", SCHEMA)
    assert sdk.openai.call_args.kwargs["api_key"] == "new-key"
    sdk.identity.assert_not_called()


def test_legacy_local_omitted_url_uses_loopback(settings, sdk):
    settings.FLOOR_PLAN_VISION_PROVIDER = "local_openai"
    settings.FLOOR_PLAN_VISION_MODEL = "legacy-local"
    adapter = vision.get_floor_plan_vision_provider()
    adapter.analyze(PNG, "image/png", SCHEMA)
    assert sdk.openai.call_args.kwargs["base_url"] == "http://127.0.0.1:11434/v1/"


def test_legacy_provider_without_model_is_unavailable(settings, sdk):
    settings.FLOOR_PLAN_VISION_PROVIDER = "azure_foundry"
    assert vision.list_floor_plan_vision_profiles() == {"defaultProfileId": None, "profiles": []}
    assert vision.resolve_vision_selection()["provider"] == "unavailable"
    with pytest.raises(ValueError):
        vision.resolve_vision_selection("default")
    with pytest.raises(ProviderUnavailable):
        vision.get_floor_plan_vision_provider().analyze(PNG, "image/png", SCHEMA)
    sdk.identity.assert_not_called()
    sdk.openai.assert_not_called()


@pytest.mark.parametrize("content", [
    "", "not-json", "[]", "{}", '{"floorPlans": "wrong"}',
    '{"floorPlans": [{"confidence": "0.8"}]}', '{"floorPlans": [{"confidence": 2}]}',
    '{"floorPlans": [{}]}', '{"floorPlans": [], "extra": true}',
    '{"floorPlans": [{"confidence": NaN}]}', '{"floorPlans": [{"confidence": Infinity}]}',
    '{"floorPlans": [{"confidence": -Infinity}]}', '{"floorPlans": [{"confidence": 1e999}]}',
    '{"floorPlans": [], "floorPlans": []}', None,
])
def test_invalid_schema_and_nonfinite_json_are_rejected(profiles, sdk, content):
    sdk.response.choices[0].message.content = content
    with pytest.raises(InvalidProviderResponse):
        vision.get_floor_plan_vision_provider().analyze(PNG, "image/png", SCHEMA)


@pytest.mark.parametrize("finish_reason,refusal", [("length", None), ("content_filter", None), (None, None), ("stop", "refused")])
def test_truncation_and_refusal_are_rejected(profiles, sdk, finish_reason, refusal):
    sdk.response.choices[0].finish_reason = finish_reason
    sdk.response.choices[0].message.refusal = refusal
    with pytest.raises(InvalidProviderResponse):
        vision.get_floor_plan_vision_provider().analyze(PNG, "image/png", SCHEMA)


def test_missing_choices_are_rejected(profiles, sdk):
    sdk.response.choices = []
    with pytest.raises(InvalidProviderResponse):
        vision.get_floor_plan_vision_provider().analyze(PNG, "image/png", SCHEMA)


def test_response_size_is_bounded(profiles, sdk, monkeypatch):
    monkeypatch.setattr(vision, "_MAX_RESPONSE_BYTES", 10)
    with pytest.raises(InvalidProviderResponse):
        vision.get_floor_plan_vision_provider().analyze(PNG, "image/png", SCHEMA)


def test_input_size_and_schema_are_checked_before_sdk(profiles, sdk, monkeypatch):
    provider = vision.get_floor_plan_vision_provider()
    monkeypatch.setattr(vision, "_MAX_IMAGE_BYTES", 1)
    with pytest.raises(InvalidProviderResponse):
        provider.analyze(PNG, "image/png", SCHEMA)
    monkeypatch.setattr(vision, "_MAX_IMAGE_BYTES", len(PNG))
    with pytest.raises(InvalidProviderResponse):
        provider.analyze(PNG, "text/html", SCHEMA)
    with pytest.raises(InvalidProviderResponse):
        provider.analyze(PNG, "image/png", {"type": "not-a-type"})
    monkeypatch.setattr(vision, "_MAX_SCHEMA_BYTES", 1)
    with pytest.raises(InvalidProviderResponse):
        provider.analyze(PNG, "image/png", SCHEMA)
    sdk.openai.assert_not_called()


def test_external_schema_references_cannot_fetch_network(profiles, sdk):
    schema = deepcopy(SCHEMA)
    schema["properties"]["floorPlans"]["items"] = {"$ref": "https://untrusted.example/schema"}
    with pytest.raises(InvalidProviderResponse):
        vision.get_floor_plan_vision_provider().analyze(PNG, "image/png", schema)


def test_sdk_failure_is_safe_and_closes_clients(profiles, sdk):
    sdk.client.chat.completions.create.side_effect = RuntimeError("https://private.example?secret=value")
    with pytest.raises(ProviderUnavailable) as error:
        vision.get_floor_plan_vision_provider().analyze(PNG, "image/png", SCHEMA)
    assert "private.example" not in str(error.value)
    assert "secret" not in str(error.value)
    sdk.client.__exit__.assert_called_once()
    sdk.transport.__exit__.assert_called_once()
