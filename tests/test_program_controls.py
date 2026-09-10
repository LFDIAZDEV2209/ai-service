"""Tests del control de programa conversacional (UC-001 'Controles', U6).

Cubre:
- Comportamiento byte-idéntico al actual cuando `control_context` está ausente
  (prompt y respuesta iguales, sin señal de control).
- Inyección de la guía por turno (bloque de sistema en español, voseo) cuando
  el backend envía `control_context`.
- Señal de rechazo `control_signal: "declined"` (sync + SSE) emitida SOLO
  cuando el modelo llama la tool no-op `mark_control_declined` Y el turno trae
  contexto de control.
- Contexto malformado → 422 (nunca 500).

Todo con modelos fake (sin API keys), patrón de test_appointment_suggestion.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver

from app.agents.prompts import BASE_SYSTEM_PROMPT, build_control_guidance
from app.api.deps import get_db_session, get_graph
from app.api.main import create_app
from app.core.config import get_settings
from app.graph.graph import build_graph
from tests.fakes import FakeToolAwareModel, RecordingFakeModel
from tests.test_api import FakeDbSession

INTERNAL_KEY = get_settings().internal_api_key
HEADERS = {"X-Internal-Key": INTERNAL_KEY}

CONTROL_CONTEXT = {
    "send_id": "3f2e9c1a-7b4d-4a8e-9c2f-1d5e6a7b8c90",
    "milestone_day": 3,
    "status": "sent",
    "exam_pending": True,
}

FAKE_ANSWER = "Respuesta de prueba"
DECLINED_ANSWER = (
    "Te entiendo perfectamente, gracias por contármelo. No hay problema: "
    "respeto tu decisión. Si algún día cambiás de opinión, podés subir tu "
    "examen desde el botón de adjuntar del chat."
)
TOOL_CALL_NAME = "mark_control_declined"


def _declined_model() -> FakeToolAwareModel:
    """Fake que primero llama a `mark_control_declined` y luego responde con calidez."""
    return FakeToolAwareModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": TOOL_CALL_NAME,
                        "args": {},
                        "id": "call_declined",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content=DECLINED_ANSWER),
        ]
    )


@pytest.fixture
def client():
    app = create_app()
    fake = FakeToolAwareModel(responses=[AIMessage(content=FAKE_ANSWER) for _ in range(5)])

    def override():
        return build_graph(model=fake, checkpointer=MemorySaver())

    app.dependency_overrides[get_graph] = override
    app.dependency_overrides[get_db_session] = FakeDbSession
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def declined_client():
    app = create_app()
    fake = _declined_model()

    def override():
        return build_graph(model=fake, checkpointer=MemorySaver())

    app.dependency_overrides[get_graph] = override
    app.dependency_overrides[get_db_session] = FakeDbSession
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


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


# --- (a) Regresión: sin control_context, comportamiento byte-idéntico --------


def test_absent_context_prompt_and_payload_identical_to_baseline(recording_client):
    client, fake = recording_client
    res = client.post("/api/v1/chat", json={"message": "hola"}, headers=HEADERS)
    assert res.status_code == 200
    body = res.json()
    assert body["answer"] == FAKE_ANSWER
    assert body["agent"] == "base"
    assert body.get("control_signal") is None

    # Prompt byte-idéntico al actual: solo el sistema base + el humano.
    assert len(fake.prompts) == 1
    prompt = fake.prompts[0]
    assert [type(m).__name__ for m in prompt] == ["SystemMessage", "HumanMessage"]
    assert prompt[0].content == BASE_SYSTEM_PROMPT
    assert prompt[-1].content == "hola"


def test_absent_context_stream_has_no_control_signal(client):
    with client.stream(
        "POST", "/api/v1/chat/stream", json={"message": "hola"}, headers=HEADERS
    ) as res:
        assert res.status_code == 200
        text = "".join(res.iter_text())

    assert "control_signal" not in text
    assert "event: done" in text


# --- (b) Guía: control_context presente inyecta el bloque por turno ----------


def test_control_context_injects_guidance_verbatim(recording_client):
    client, fake = recording_client
    res = client.post(
        "/api/v1/chat",
        json={"message": "Hoy me siento un poco cansado", "control_context": CONTROL_CONTEXT},
        headers=HEADERS,
    )
    assert res.status_code == 200

    prompt = fake.prompts[0]
    expected = build_control_guidance(day=3, status="sent", exam_pending=True)
    assert any(isinstance(m, SystemMessage) and m.content == expected for m in prompt)
    # El bloque base permanece intacto y la guía es un SystemMessage extra.
    assert prompt[0].content == BASE_SYSTEM_PROMPT


def test_guidance_is_only_addition_when_context_present(recording_client):
    client, fake = recording_client
    client.post("/api/v1/chat", json={"message": "hola"}, headers=HEADERS)
    client.post(
        "/api/v1/chat",
        json={"message": "hola", "control_context": CONTROL_CONTEXT},
        headers=HEADERS,
    )

    baseline, guided = fake.prompts[0], fake.prompts[1]
    assert len(guided) == len(baseline) + 1
    assert guided[0].content == baseline[0].content  # sistema base idéntico
    assert guided[-1].content == baseline[-1].content  # humano idéntico
    assert isinstance(guided[1], SystemMessage)
    assert guided[1].content == build_control_guidance(day=3, status="sent", exam_pending=True)


def test_guidance_block_static_contract():
    block = build_control_guidance(day=3, status="sent", exam_pending=True)
    assert len(block.splitlines()) < 15
    assert "adjuntar" in block  # botón de adjuntar del chat
    assert "segundos" in block  # panel completo, tarda segundos
    assert TOOL_CALL_NAME in block  # señal de rechazo vía tool
    assert "escuchá" in block and "sin juzgar" in block  # escucha empática, voseo
    assert "no inventes" in block  # nunca inventar números


def test_guidance_followed_up_is_last_reminder():
    block = build_control_guidance(day=3, status="followed_up", exam_pending=True)
    assert "último recordatorio" in block


def test_guidance_without_pending_exam_does_not_ask_upload():
    block = build_control_guidance(day=3, status="sent", exam_pending=False)
    assert "adjuntar" not in block
    assert "examen" in block
    assert TOOL_CALL_NAME not in block


# --- (c) Señal de rechazo: sync -------------------------------------------------


def test_explicit_refusal_emits_control_signal_sync(declined_client):
    res = declined_client.post(
        "/api/v1/chat",
        json={"message": "No, gracias, no quiero subir nada", "control_context": CONTROL_CONTEXT},
        headers=HEADERS,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["control_signal"] == "declined"
    assert body["answer"] == DECLINED_ANSWER
    assert TOOL_CALL_NAME in body["tools_used"]
    # La señal nunca se filtra en el texto mostrado al paciente.
    assert TOOL_CALL_NAME not in body["answer"]


def test_no_refusal_emits_no_control_signal_sync(client):
    res = client.post(
        "/api/v1/chat",
        json={"message": "Bien, ¿y vos?", "control_context": CONTROL_CONTEXT},
        headers=HEADERS,
    )
    assert res.status_code == 200
    assert res.json().get("control_signal") is None


def test_tool_without_context_never_emits_signal(declined_client):
    # La tool se ejecuta, pero sin control_context el contrato es byte-idéntico:
    # nunca se emite la señal.
    res = declined_client.post("/api/v1/chat", json={"message": "No quiero"}, headers=HEADERS)
    assert res.status_code == 200
    body = res.json()
    assert TOOL_CALL_NAME in body["tools_used"]
    assert body.get("control_signal") is None


# --- (d) Señal de rechazo: SSE ---------------------------------------------------


def test_explicit_refusal_emits_control_signal_stream_at_correct_position(declined_client):
    with declined_client.stream(
        "POST",
        "/api/v1/chat/stream",
        json={"message": "No, gracias", "control_context": CONTROL_CONTEXT},
        headers=HEADERS,
    ) as res:
        assert res.status_code == 200
        text = "".join(res.iter_text())

    assert "event: control_signal\ndata: declined" in text
    # La señal va DESPUÉS de que termina el stream de tokens y ANTES de `done`.
    last_token = text.rfind('"type": "token"')
    signal_pos = text.find("event: control_signal")
    done_pos = text.find("event: done")
    assert last_token != -1
    assert signal_pos > last_token
    assert done_pos > signal_pos
    # El nombre de la tool nunca aparece en el texto mostrado al paciente.
    assert TOOL_CALL_NAME not in text


def test_no_refusal_emits_no_control_signal_stream(client):
    with client.stream(
        "POST",
        "/api/v1/chat/stream",
        json={"message": "Bien", "control_context": CONTROL_CONTEXT},
        headers=HEADERS,
    ) as res:
        assert res.status_code == 200
        text = "".join(res.iter_text())

    assert "control_signal" not in text
    assert "event: done" in text


# --- (e) Validación: contexto malformado ------------------------------------------


def test_malformed_control_context_rejected_not_500(client):
    res = client.post(
        "/api/v1/chat",
        json={"message": "hola", "control_context": {"send_id": "solo-esto"}},
        headers=HEADERS,
    )
    assert res.status_code == 422