"""Tests del idioma explícito por turno (`ChatRequest.language`).

Cubre:
- Comportamiento byte-idéntico al actual cuando `language` está ausente o es
  desconocido (prompt y respuesta iguales, sin bloque de idioma).
- Inyección del bloque de idioma (`build_language_instruction`) como
  SystemMessage extra cuando el frontend envía `language` ('es' | 'en').
- Normalización de la señal (mayúsculas/espacios; valor inválido → None).
- Cambio de idioma a mitad de thread: el bloque nuevo GANA sobre el historial
  (cada turno trae su propia señal, nunca persiste en el checkpointer).

Todo con modelos fake (sin API keys), patrón de test_program_controls.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver

from app.agents.prompts import BASE_SYSTEM_PROMPT, build_language_instruction
from app.api.deps import get_db_session, get_graph
from app.api.main import create_app
from app.api.routes.chat import _normalize_language, _rate_limiters
from app.core.config import get_settings
from app.graph.graph import build_graph
from tests.fakes import RecordingFakeModel
from tests.test_api import FakeDbSession

INTERNAL_KEY = get_settings().internal_api_key
HEADERS = {"X-Internal-Key": INTERNAL_KEY}

FAKE_ANSWER = "Respuesta de prueba"


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """Aísla el rate limiter global entre tests (mismo motivo que test_program_controls)."""
    _rate_limiters.clear()
    yield
    _rate_limiters.clear()


@pytest.fixture
def recording_client():
    """Cliente con modelo que captura los prompts; expone (client, fake)."""
    app = create_app()
    fake = RecordingFakeModel(responses=[AIMessage(content=FAKE_ANSWER) for _ in range(5)])

    def override():
        return build_graph(model=fake, checkpointer=MemorySaver())

    app.dependency_overrides[get_graph] = override
    app.dependency_overrides[get_db_session] = FakeDbSession
    with TestClient(app) as test_client:
        yield test_client, fake
    app.dependency_overrides.clear()


# --- (a) Regresión: sin language, comportamiento byte-idéntico ----------------


def test_absent_language_prompt_identical_to_baseline(recording_client):
    client, fake = recording_client
    res = client.post("/api/v1/chat", json={"message": "hola"}, headers=HEADERS)
    assert res.status_code == 200
    assert res.json()["answer"] == FAKE_ANSWER

    # Prompt byte-idéntico al estándar: solo el sistema base + el humano.
    assert len(fake.prompts) == 1
    prompt = fake.prompts[0]
    assert [type(m).__name__ for m in prompt] == ["SystemMessage", "HumanMessage"]
    assert prompt[0].content == BASE_SYSTEM_PROMPT
    assert prompt[-1].content == "hola"


@pytest.mark.parametrize("raw", ["fr", "de", "", "  "])
def test_unknown_language_is_ignored(recording_client, raw):
    """Idioma no soportado ⇒ sin bloque (espejo estándar, nunca rompe)."""
    client, fake = recording_client
    res = client.post("/api/v1/chat", json={"message": "hola", "language": raw}, headers=HEADERS)
    assert res.status_code == 200
    assert len(fake.prompts) == 1
    assert [type(m).__name__ for m in fake.prompts[0]] == [
        "SystemMessage",
        "HumanMessage",
    ]


# --- (b) Señal explícita: inyecta el bloque de idioma por turno ---------------


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        ("en", build_language_instruction("en")),
        ("es", build_language_instruction("es")),
    ],
)
def test_language_injects_block(recording_client, lang, expected):
    client, fake = recording_client
    res = client.post("/api/v1/chat", json={"message": "hola", "language": lang}, headers=HEADERS)
    assert res.status_code == 200

    prompt = fake.prompts[0]
    # El bloque base permanece intacto y el idioma es un SystemMessage extra.
    assert prompt[0].content == BASE_SYSTEM_PROMPT
    assert any(isinstance(m, SystemMessage) and m.content == expected for m in prompt)


def test_language_normalization(recording_client):
    """Mayúsculas/espacios se normalizan; solo 'es'/'en' son válidos."""
    assert _normalize_language(" EN ") == "en"
    assert _normalize_language("Es") == "es"
    assert _normalize_language(None) is None
    assert _normalize_language("fr") is None


def test_language_switch_mid_thread(recording_client):
    """Cambio de idioma a mitad de thread: el turno trae el bloque nuevo.

    La señal viaja por `configurable` (no por state): el checkpointer nunca
    la persiste, así que el turno siguiente refleja el idioma actual de la
    app, no el del historial.
    """
    client, fake = recording_client
    res1 = client.post("/api/v1/chat", json={"message": "hola", "language": "es"}, headers=HEADERS)
    res2 = client.post(
        "/api/v1/chat",
        json={"message": "hello there", "language": "en"},
        headers=HEADERS,
    )
    assert res1.status_code == 200
    assert res2.status_code == 200

    first, second = fake.prompts[0], fake.prompts[1]
    assert any(
        isinstance(m, SystemMessage) and m.content == build_language_instruction("es")
        for m in first
    )
    assert any(
        isinstance(m, SystemMessage) and m.content == build_language_instruction("en")
        for m in second
    )
    assert not any(
        isinstance(m, SystemMessage) and m.content == build_language_instruction("es")
        for m in second
    )
