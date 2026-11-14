"""Sesiones con versión verificada y transporte falso; ninguna llamada al proveedor real."""

import json
from copy import deepcopy
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents.prompts import VOICE_FIRST_MESSAGES, build_patient_voice_prompt
from app.api.main import create_app
from app.core.config import get_settings
from app.voice import client as voice_client

URL = "/internal/voice/session"
HEADERS = {"X-Internal-Key": get_settings().internal_api_key}
VERSIONS = {"es": "agtvrsn_test_es", "en": "agtvrsn_test_en"}


def _configuration(language):
    return {
        "agent_id": "agent_test",
        "version_id": VERSIONS[language],
        "conversation_config": {
            "agent": {
                "language": language,
                "first_message": VOICE_FIRST_MESSAGES[language],
                "prompt": {"prompt": build_patient_voice_prompt(language)},
            }
        },
        "platform_settings": {"auth": {"enable_auth": True}, "overrides": {}},
    }


@pytest.fixture(autouse=True)
def controlled_settings(monkeypatch):
    monkeypatch.setenv("AUTO_MIGRATE", "false")
    monkeypatch.setenv("VOICE_ENABLED", "true")
    monkeypatch.setenv("VOICE_REVIEW_VERIFICATION_ENABLED", "true")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test_no_real")
    monkeypatch.setenv("ELEVENLABS_AGENT_ID", "agent_test")
    monkeypatch.setenv("ELEVENLABS_BASE_URL", "https://api.elevenlabs.test")
    for language, version in VERSIONS.items():
        monkeypatch.setenv(f"ELEVENLABS_PATIENT_VERSION_{language.upper()}", version)
    monkeypatch.setenv("ELEVENLABS_PATIENT_TOOL_CONFIG_SHA256", "{}")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)

    class PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("transport", transport)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(voice_client.httpx, "AsyncClient", PatchedClient)


def _post(language="es", headers=HEADERS):
    with TestClient(create_app()) as client:
        return client.post(URL, json={"user_id": "u-test", "language": language}, headers=headers)


def test_requires_internal_key(monkeypatch):
    _transport(monkeypatch, lambda _: pytest.fail("must not contact provider"))
    assert _post(headers={}).status_code == 401


def test_provider_url_only_response_starts_linked_legacy_session(monkeypatch):
    """Forma real de ElevenLabs US: ID reservado dentro del URL, no en el JSON."""
    store = AsyncMock()
    monkeypatch.setattr("app.voice.vita_session.voice_store.create", store)

    def handler(request):
        if request.url.path.endswith("/agents/agent_test"):
            return httpx.Response(200, json=_configuration("es"))
        assert request.url.params["include_conversation_id"] == "true"
        return httpx.Response(
            200,
            json={
                "signed_url": (
                    "wss://api.us.elevenlabs.io/v1/convai/conversation?agent_id=agent_test"
                    "&conversation_signature=synthetic-only&conversation_id=conv_reserved"
                )
            },
        )

    _transport(monkeypatch, handler)
    with TestClient(create_app()) as client:
        response = client.post(
            URL,
            json={
                "user_id": "u-test",
                "thread_id": "thread-test",
                "language": "es",
                "supports_vita_voice": True,
            },
            headers=HEADERS,
        )
    assert response.status_code == 200
    assert response.json()["conversation_id"] == "conv_reserved"
    assert response.json()["runtime_mode"] == "legacy"
    assert response.json()["session_id"]
    store.assert_awaited_once()
    assert store.call_args.args[0].conversation_id == "conv_reserved"


@pytest.mark.parametrize(
    "body_id,query,expected",
    [
        (None, "conversation_id=conv_reserved", "conv_reserved"),
        ("conv_reserved", "conversation_id=conv_reserved", "conv_reserved"),
        ("conv_reserved", "", "conv_reserved"),
        (None, "", None),
    ],
)
async def test_signed_url_conversation_id_compatibility(monkeypatch, body_id, query, expected):
    _transport(
        monkeypatch,
        lambda _: httpx.Response(
            200,
            json={"signed_url": f"wss://signed.example/token?{query}", "conversation_id": body_id},
        ),
    )
    result = await voice_client.create_signed_url(get_settings(), "agent_test")
    assert result["conversation_id"] == expected


@pytest.mark.parametrize(
    "body_id,query",
    [
        ("conv_other", "conversation_id=conv_reserved"),
        (None, "conversation_id=conv_one&conversation_id=conv_two"),
        (None, "conversation_id="),
        (None, "conversation_id=conv_%0Ainvalid"),
        (None, "conversation_id=" + "x" * 129),
    ],
)
async def test_ambiguous_or_invalid_reserved_id_fails_closed(monkeypatch, body_id, query):
    _transport(
        monkeypatch,
        lambda _: httpx.Response(
            200,
            json={"signed_url": f"wss://signed.example/token?{query}", "conversation_id": body_id},
        ),
    )
    with pytest.raises(voice_client.VoiceSessionError) as caught:
        await voice_client.create_signed_url(get_settings(), "agent_test")
    assert caught.value.status == 502
    assert "wss://" not in str(caught.value)


@pytest.mark.parametrize("variable", ["VOICE_ENABLED", "ELEVENLABS_PATIENT_VERSION_ES"])
def test_missing_approved_configuration_never_issues_credentials(monkeypatch, variable):
    monkeypatch.setenv(variable, "false" if variable == "VOICE_ENABLED" else "")
    get_settings.cache_clear()
    _transport(monkeypatch, lambda _: pytest.fail("must not contact provider"))
    assert _post().status_code == 503


@pytest.mark.parametrize(
    "language,normalized", [("es", "es"), ("en", "en"), (None, "es"), ("fr", "es")]
)
def test_verifies_then_signs_the_same_version_without_shared_patch(
    monkeypatch, language, normalized
):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.headers["xi-api-key"] == "sk_test_no_real"
        assert request.url.params["version_id"] == VERSIONS[normalized]
        if request.url.path.endswith("/agents/agent_test"):
            return httpx.Response(200, json=_configuration(normalized))
        assert request.url.path.endswith("/conversation/get-signed-url")
        assert request.url.params["agent_id"] == "agent_test"
        assert "enable_conversation_config_override" not in request.url.params
        return httpx.Response(
            200, json={"signed_url": "wss://signed.example/token", "conversation_id": "c-test"}
        )

    _transport(monkeypatch, handler)
    response = _post(language)
    assert response.status_code == 200
    assert response.json() == {
        "signed_url": "wss://signed.example/token",
        "conversation_id": "c-test",
        "agent_id": "agent_test",
    }
    assert len(calls) == 2
    assert calls[0].url.path.endswith("/agents/agent_test")


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(version_id="agtvrsn_wrong"),
        lambda p: p.update(agent_id="wrong"),
        lambda p: p["conversation_config"]["agent"]["prompt"].update(prompt="Elige dosis"),
        lambda p: p["conversation_config"]["agent"].update(language="en"),
        lambda p: p["conversation_config"]["agent"].update(first_message="Soy médico"),
        lambda p: p["platform_settings"]["auth"].update(enable_auth=False),
        lambda p: p["platform_settings"].update(
            overrides={"conversation_config_override": {"agent": {"prompt": {"prompt": True}}}}
        ),
        lambda p: p["platform_settings"].update(overrides="unknown"),
        lambda p: p.update(workflow={"nodes": {"subagent": {"prompt": "other"}}}),
        lambda p: p.update(procedures=["proc_other"]),
        lambda p: p["conversation_config"]["agent"]["prompt"].update(
            knowledge_base=[{"id": "clinical"}]
        ),
        lambda p: p["conversation_config"]["agent"]["prompt"].update(tools=[{"type": "webhook"}]),
        lambda p: p["conversation_config"]["agent"]["prompt"].update(rag={"enabled": True}),
        lambda p: p["conversation_config"]["agent"]["prompt"].update(tool_ids=["../other"]),
        lambda p: p["conversation_config"]["agent"]["prompt"].update(tool_ids="tool_one"),
        lambda p: p["conversation_config"]["agent"]["prompt"].update(
            tool_ids=["tool_one", "tool_one"]
        ),
        lambda p: p["conversation_config"].update(language_presets={"en": {}}),
        lambda p: p["conversation_config"]["agent"]["prompt"].update(native_mcp_server_ids=["mcp"]),
        lambda p: p["conversation_config"]["agent"]["prompt"].update(
            built_in_tools={"transfer_to_agent": {"type": "system"}}
        ),
        lambda p: p["platform_settings"].update(
            workspace_overrides={
                "conversation_initiation_client_data_webhook": {"url": "https://other"}
            }
        ),
    ],
)
def test_scope_or_override_mismatch_fails_closed_before_signed_url(monkeypatch, change):
    payload = deepcopy(_configuration("es"))
    change(payload)
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path.endswith("/agents/agent_test")
        return httpx.Response(200, json=payload)

    _transport(monkeypatch, handler)
    response = _post()
    assert response.status_code == 503
    assert response.json()["detail"] == "La voz no está disponible en este momento."
    assert len(calls) == 1


@pytest.mark.parametrize("body", [None, [], {}, {"conversation_config": []}])
def test_malformed_verification_response_fails_closed(monkeypatch, body):
    _transport(monkeypatch, lambda _: httpx.Response(200, json=body))
    assert _post().status_code == 503


@pytest.mark.parametrize("status", [401, 404, 500])
def test_unavailable_verification_fails_closed_without_credentials(monkeypatch, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="secret provider diagnostic")

    _transport(monkeypatch, handler)
    response = _post()
    assert response.status_code == 503
    assert "secret" not in response.text
    assert len(calls) == 1


def test_verification_timeout_fails_closed(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("private transport details", request=request)

    _transport(monkeypatch, handler)
    response = _post()
    assert response.status_code == 503
    assert "private" not in response.text


@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        {"signed_url": "https://unsafe"},
        {"signed_url": "wss://[invalid"},
        {"signed_url": "wss://ok", "conversation_id": 12},
    ],
)
def test_malformed_signed_url_is_safe_upstream_error(monkeypatch, body):
    def handler(request):
        if request.url.path.endswith("/agents/agent_test"):
            return httpx.Response(200, json=_configuration("es"))
        return httpx.Response(200, json=body)

    _transport(monkeypatch, handler)
    response = _post()
    assert response.status_code == 502
    assert "signed_url" not in response.text


async def test_concurrent_locales_pin_each_request_and_reverify_without_cache(monkeypatch):
    import asyncio

    from app.api.routes.voice import create_voice_session
    from app.voice.schemas import VoiceSessionRequest

    calls = []

    async def handler(request):
        version = request.url.params["version_id"]
        language = next(k for k, v in VERSIONS.items() if v == version)
        calls.append((request.url.path, version))
        if request.url.path.endswith("/agents/agent_test"):
            await asyncio.sleep(0)
            return httpx.Response(200, json=_configuration(language))
        return httpx.Response(200, json={"signed_url": f"wss://signed.example/{language}"})

    _transport(monkeypatch, handler)
    responses = await asyncio.gather(
        *[
            create_voice_session(VoiceSessionRequest(user_id="u-test", language=language))
            for language in ("es", "en", "es")
        ]
    )
    assert [r.signed_url for r in responses] == [
        "wss://signed.example/es",
        "wss://signed.example/en",
        "wss://signed.example/es",
    ]
    assert len(calls) == 6
    assert sum(path.endswith("/agents/agent_test") for path, _ in calls) == 3


@pytest.mark.parametrize(
    "failure", [None, "unapproved", "changed", "server", "wrong_name", "mock", "unavailable"]
)
def test_legacy_appointment_tools_are_rejected_even_when_reviewed(monkeypatch, failure):
    tool_id = "tool_appointment"
    tool_config = {
        "type": "client",
        "name": "get_my_upcoming_appointments",
        "description": "Read own appointments",
        "expects_response": True,
        "parameters": {"type": "object", "properties": {}},
    }
    approved = voice_client.patient_tool_config_digest(tool_config)
    # Unapproved tools fail before a tool lookup; all others must be re-read.
    monkeypatch.setenv(
        "ELEVENLABS_PATIENT_TOOL_CONFIG_SHA256",
        json.dumps({} if failure == "unapproved" else {tool_id: approved}),
    )
    get_settings.cache_clear()
    provider_config = deepcopy(tool_config)
    if failure == "changed":
        provider_config["description"] = "Changed after review"
    if failure == "server":
        provider_config["type"] = "webhook"
    if failure == "wrong_name":
        provider_config["name"] = "prescribe_drug"
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/agents/agent_test"):
            version = _configuration("es")
            version["conversation_config"]["agent"]["prompt"]["tool_ids"] = [tool_id]
            return httpx.Response(200, json=version)
        if request.url.path.endswith("/tools/" + tool_id):
            return httpx.Response(
                500 if failure == "unavailable" else 200,
                json={
                    "id": tool_id,
                    "tool_config": provider_config,
                    "response_mocks": [{"mock_result": "take drug"}] if failure == "mock" else [],
                },
            )
        assert failure is None, "unapproved definitions must not obtain credentials"
        return httpx.Response(200, json={"signed_url": "wss://signed.example/approved"})

    _transport(monkeypatch, handler)
    for _ in range(2):
        response = _post()
        assert response.status_code == 503
    assert calls.count("/v1/convai/agents/agent_test") == 2
    assert calls.count("/v1/convai/tools/" + tool_id) == (0 if failure == "unapproved" else 2)


def test_tool_config_digest_is_canonical_and_sensitive_to_schema():
    config = {"type": "client", "name": "list_specialties", "description": "á"}
    assert voice_client.patient_tool_config_digest(
        config
    ) == voice_client.patient_tool_config_digest(dict(reversed(list(config.items()))))
    assert voice_client.patient_tool_config_digest(
        config
    ) != voice_client.patient_tool_config_digest({**config, "parameters": {}})


def test_restored_voice_uses_configured_agent_without_review_pins(monkeypatch):
    monkeypatch.setenv("VOICE_REVIEW_VERIFICATION_ENABLED", "false")
    monkeypatch.delenv("ELEVENLABS_PATIENT_VERSION_ES", raising=False)
    monkeypatch.delenv("ELEVENLABS_PATIENT_VERSION_EN", raising=False)
    get_settings.cache_clear()
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/v1/convai/conversation/get-signed-url"
        assert request.url.params["agent_id"] == "agent_test"
        assert "version_id" not in request.url.params
        return httpx.Response(200, json={"signed_url": "wss://synthetic.invalid/session"})

    _transport(monkeypatch, handler)
    response = _post()
    assert response.status_code == 200
    assert len(calls) == 1
    assert _post(headers={}).status_code in (401, 403)
    assert len(calls) == 1
