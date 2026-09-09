"""Tests del CTA de agenda de cita (`suggest_appointment`).

Cubre: captura en estado del grafo, contrato en /chat (sync), evento `done`
del streaming, y validación de `urgency` de la tool. Todo con modelos fake
(sin API keys).
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from app.api.deps import get_db_session, get_graph
from app.api.main import create_app
from app.core.config import get_settings
from app.core.errors import ToolExecutionError
from app.graph.graph import build_graph
from app.tools.appointment import suggest_appointment
from tests.fakes import FakeToolAwareModel
from tests.test_api import FakeDbSession

INTERNAL_KEY = get_settings().internal_api_key
HEADERS = {"X-Internal-Key": INTERNAL_KEY}

SUGGESTION_ARGS = {
    "reason": "Dolor de cabeza persistente hace más de una semana",
    "urgency": "alta",
}
EXPECTED_SUGGESTION = {
    "type": "appointment",
    "cta_text": "Agenda tu cita aquí",
    "reason": SUGGESTION_ARGS["reason"],
    "urgency": "alta",
}


def _tool_call_model() -> FakeToolAwareModel:
    """Fake que primero llama a `suggest_appointment` y luego responde texto."""
    return FakeToolAwareModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "suggest_appointment",
                        "args": SUGGESTION_ARGS,
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Te recomiendo agendar una cita con tu médico."),
        ]
    )


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 10}


# --- (a) Grafo: la sugerencia se acumula en state["suggestions"] ------------


@pytest.mark.asyncio
async def test_graph_captures_appointment_suggestion():
    graph = build_graph(model=_tool_call_model(), checkpointer=MemorySaver())
    result = await graph.ainvoke(
        {"input": "Tengo dolor de cabeza hace días y no se me pasa"},
        config=_config("t-suggestion"),
    )

    assert result["tools_used"] == ["suggest_appointment"]
    assert result["suggestions"] == [EXPECTED_SUGGESTION]
    assert result["messages"][-1].content == "Te recomiendo agendar una cita con tu médico."


@pytest.mark.asyncio
async def test_graph_without_suggestion_keeps_list_empty():
    fake = FakeToolAwareModel(responses=[AIMessage(content="Respuesta normal.")])
    graph = build_graph(model=fake, checkpointer=MemorySaver())
    result = await graph.ainvoke({"input": "¿Cómo estás?"}, config=_config("t-no-suggestion"))

    assert result.get("suggestions", []) == []


# --- (b) API sync: /chat devuelve el array de sugerencias -------------------


@pytest.fixture
def suggestion_client():
    app = create_app()

    def override():
        return build_graph(model=_tool_call_model(), checkpointer=MemorySaver())

    app.dependency_overrides[get_graph] = override
    app.dependency_overrides[get_db_session] = FakeDbSession
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_chat_returns_suggestions_array(suggestion_client):
    res = suggestion_client.post(
        "/api/v1/chat",
        json={"message": "Llevo días con dolor y quería saber qué hago"},
        headers=HEADERS,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["suggestions"] == [EXPECTED_SUGGESTION]


@pytest.fixture
def plain_client():
    """Cliente con grafo fake que NO llama tools (verifica array vacío)."""
    app = create_app()
    fake = FakeToolAwareModel(responses=[AIMessage(content="Respuesta normal.")])
    app.dependency_overrides[get_graph] = lambda: build_graph(
        model=fake, checkpointer=MemorySaver()
    )
    app.dependency_overrides[get_db_session] = FakeDbSession
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_chat_without_suggestions_returns_empty_array(plain_client):
    res = plain_client.post("/api/v1/chat", json={"message": "hola"}, headers=HEADERS)
    assert res.status_code == 200
    assert res.json()["suggestions"] == []


# --- (c) API stream: el evento done incluye las sugerencias -----------------


def _parse_sse_done(text: str) -> dict:
    """Extrae el data JSON del evento `done` de un stream SSE."""
    for block in text.split("\n\n"):
        if block.startswith("event: done"):
            data_line = next(line for line in block.splitlines() if line.startswith("data: "))
            return json.loads(data_line[len("data: ") :])
    raise AssertionError("No se encontró el evento done en el stream")


def test_chat_stream_done_includes_suggestions(suggestion_client):
    with suggestion_client.stream(
        "POST",
        "/api/v1/chat/stream",
        json={"message": "Quiero agendar una cita con el doctor"},
        headers=HEADERS,
    ) as res:
        assert res.status_code == 200
        text = "".join(res.iter_text())
        done = _parse_sse_done(text)

    assert done["thread_id"]
    assert done["execution_id"]
    assert done["suggestions"] == [EXPECTED_SUGGESTION]


# --- (d) Tool: validación de urgency ----------------------------------------


def test_suggest_appointment_rejects_invalid_urgency():
    with pytest.raises(ToolExecutionError):
        suggest_appointment.invoke({"reason": "Dolor", "urgency": "urgente"})


def test_suggest_appointment_accepts_valid_urgencies():
    for urgency in ("normal", "alta"):
        raw = suggest_appointment.invoke({"reason": "Dolor", "urgency": urgency})
        parsed = json.loads(raw)
        assert parsed["type"] == "appointment"
        assert parsed["cta_text"] == "Agenda tu cita aquí"
        assert parsed["urgency"] == urgency
        assert parsed["reason"] == "Dolor"


def test_suggest_appointment_defaults():
    raw = suggest_appointment.invoke({"reason": "Malestar emocional sostenido"})
    parsed = json.loads(raw)
    assert parsed["urgency"] == "normal"
    assert parsed["cta_text"] == "Agenda tu cita aquí"
