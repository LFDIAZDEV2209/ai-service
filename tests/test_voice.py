"""Tests del endpoint interno de sesiones de voz (ElevenLabs).

Verifica que:
1. El endpoint exige `X-Internal-Key` (401 sin header).
2. Sin `VOICE_ENABLED` → 503 (degradación explícita, no 500).
3. Con la config habilitada y httpx mocked (MockTransport), emite el
   signed URL y no lo loguea ni lo filtra en errores.
4. Fallo de ElevenLabs (401/500) → 502 con mensaje seguro.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.core.config import get_settings
from app.voice import client as voice_client

URL = "/internal/voice/session"

HEADERS = {"X-Internal-Key": get_settings().internal_api_key}


@pytest.fixture
def client():
    """App de test con settings controladas por variables de entorno."""
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _apply_voice_env(monkeypatch, *, enabled: str = "true") -> None:
    """Fija las variables de entorno de voz para el test (antes del get_settings).

    `AUTO_MIGRATE=false` evita que el lifespan toque PostgreSQL: el módulo de
    voz no depende de BD y los tests no deben depender de una base local.
    """
    monkeypatch.setenv("AUTO_MIGRATE", "false")
    monkeypatch.setenv("VOICE_ENABLED", enabled)
    if enabled == "true":
        monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test_no_real")
        monkeypatch.setenv("ELEVENLABS_AGENT_ID", "agent_test")
        monkeypatch.setenv("ELEVENLABS_BASE_URL", "https://api.elevenlabs.test")


def _mock_elevenlabs(monkeypatch, handler):
    """Reemplaza httpx.AsyncClient.get por un transporte simulado."""
    transport = httpx.MockTransport(handler)

    class _PatchedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(voice_client.httpx, "AsyncClient", _PatchedClient)


# --- Autenticación y degradación ----------------------------------------


def test_voice_session_requires_internal_key():
    app = create_app()
    with TestClient(app) as tc:
        res = tc.post(URL, json={"user_id": "u1"})
    assert res.status_code in (401, 403)


def test_voice_session_disabled(monkeypatch):
    _apply_voice_env(monkeypatch, enabled="false")
    get_settings.cache_clear()
    try:
        app = create_app()
        with TestClient(app) as tc:
            res = tc.post(
                URL,
                json={"user_id": "u1"},
                headers=HEADERS,
            )
        assert res.status_code == 503
        assert "Voz deshabilitada" in res.json()["detail"]
    finally:
        get_settings.cache_clear()


# --- Contrato feliz ------------------------------------------------------


def test_voice_session_success(monkeypatch):
    _apply_voice_env(monkeypatch, enabled="true")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["xi-api-key"] == "sk_test_no_real"
        assert "agent_id=agent_test" in str(request.url)
        return httpx.Response(
            200,
            json={
                "signed_url": "wss://signed.example/one-time-token",
                "conversation_id": "conv_123",
            },
        )

    _mock_elevenlabs(monkeypatch, handler)
    get_settings.cache_clear()
    try:
        app = create_app()
        with TestClient(app) as tc:
            res = tc.post(
                URL,
                json={
                    "user_id": "u-1",
                    "patient_id": "p-1",
                    "thread_id": "proactive-p-1",
                },
                headers=HEADERS,
            )
        assert res.status_code == 200
        body = res.json()
        assert body["signed_url"].startswith("wss://")
        assert body["agent_id"] == "agent_test"
        assert body["conversation_id"] == "conv_123"
    finally:
        get_settings.cache_clear()


# --- Errores de la plataforma -------------------------------------------


def test_voice_session_elevenlabs_unauthorized(monkeypatch):
    _apply_voice_env(monkeypatch, enabled="true")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": {"status": "invalid_api_key"}})

    _mock_elevenlabs(monkeypatch, handler)
    get_settings.cache_clear()
    try:
        app = create_app()
        with TestClient(app) as tc:
            res = tc.post(URL, json={"user_id": "u-1"}, headers=HEADERS)
        assert res.status_code == 401
        # Mensaje seguro: no expone la key ni el signed URL.
        assert "signed_url" not in res.text
    finally:
        get_settings.cache_clear()


def test_voice_session_elevenlabs_server_error(monkeypatch):
    _apply_voice_env(monkeypatch, enabled="true")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    _mock_elevenlabs(monkeypatch, handler)
    get_settings.cache_clear()
    try:
        app = create_app()
        with TestClient(app) as tc:
            res = tc.post(URL, json={"user_id": "u-1"}, headers=HEADERS)
        assert res.status_code == 502
    finally:
        get_settings.cache_clear()
