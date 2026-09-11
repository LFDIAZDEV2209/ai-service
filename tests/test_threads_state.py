"""Tests del historial completo expuesto por GET /threads/{thread_id}/state.

El contrato es aditivo: `messages` lista los turnos visibles (usuario/bot) en
orden cronológico (más reciente al final) para que la app del paciente pueda
re-renderizar la conversación tras un re-login. `message_count` sigue
reportando el total real (incluidos los mensajes internos) y `last_message`
conserva el comportamiento previo del endpoint.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from app.api.deps import get_db_session, get_graph
from app.api.main import create_app
from app.core.config import get_settings
from app.graph.graph import build_graph
from tests.fakes import FakeToolAwareModel

INTERNAL_KEY = get_settings().internal_api_key
HEADERS = {"X-Internal-Key": INTERNAL_KEY}

USER_ID = "user-1"
THREAD_ID = "thread-abc"

URL = "/api/v1/threads/{thread_id}/state"


class FakeDbSession:
    """Sesión de BD fake: el endpoint de estado no toca Postgres."""

    async def add(self, *args, **kwargs):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def get(self, *args, **kwargs):
        return None

    async def execute(self, *args, **kwargs):
        return None


@pytest.fixture
def graph():
    """Grafo con LLM falso SIN respuestas: solo se usa el checkpointer."""
    fake = FakeToolAwareModel(responses=[])
    return build_graph(model=fake, checkpointer=MemorySaver())


@pytest.fixture
def client(graph):
    app = create_app()
    app.dependency_overrides[get_graph] = lambda: graph
    app.dependency_overrides[get_db_session] = FakeDbSession
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _config(thread_id: str = THREAD_ID) -> dict:
    """Misma clave interna que usa /chat (aislamiento por user_id)."""
    return {"configurable": {"thread_id": f"{USER_ID}::{thread_id}"}}


async def _seed(graph, messages: list) -> None:
    await graph.aupdate_state(_config(), {"messages": messages})


def _get(client, thread_id: str = THREAD_ID):
    return client.get(
        URL.format(thread_id=thread_id),
        params={"user_id": USER_ID},
        headers=HEADERS,
    )


def test_requires_internal_key(client):
    res = client.get(URL.format(thread_id=THREAD_ID), params={"user_id": USER_ID})
    assert res.status_code == 401


async def test_messages_maps_roles_and_preserves_order(client, graph):
    await _seed(
        graph,
        [
            HumanMessage(content="Hola"),
            AIMessage(content="¿En qué te ayudo?"),
            HumanMessage(content="Me duele la cabeza"),
            AIMessage(content="Cuéntame más"),
        ],
    )

    res = _get(client)
    assert res.status_code == 200
    body = res.json()
    assert body["messages"] == [
        {"role": "user", "text": "Hola"},
        {"role": "bot", "text": "¿En qué te ayudo?"},
        {"role": "user", "text": "Me duele la cabeza"},
        {"role": "bot", "text": "Cuéntame más"},
    ]
    assert body["message_count"] == 4
    assert body["last_message"] == "Cuéntame más"


async def test_tool_and_system_messages_excluded(client, graph):
    await _seed(
        graph,
        [
            HumanMessage(content="¿Cuánto es 2+2?"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "calculate",
                        "args": {"expression": "2+2"},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(content="4", tool_call_id="call-1"),
            SystemMessage(content="Prompt interno del agente"),
            AIMessage(content="Son 4"),
        ],
    )

    body = _get(client).json()
    assert body["messages"] == [
        {"role": "user", "text": "¿Cuánto es 2+2?"},
        {"role": "bot", "text": "Son 4"},
    ]
    # El contador reporta el total real, incluidos los mensajes internos.
    assert body["message_count"] == 5
    assert body["last_message"] == "Son 4"


async def test_tool_call_only_ai_messages_excluded(client, graph):
    await _seed(
        graph,
        [
            HumanMessage(content="Busca mi cita"),
            # AIMessage solo tool_use (estilo Anthropic): sin texto visible.
            AIMessage(
                content=[
                    {
                        "type": "tool_use",
                        "id": "call-2",
                        "name": "suggest_appointment",
                        "input": {},
                    }
                ],
            ),
            # Bloques mixtos: se conserva solo el bloque de texto.
            AIMessage(
                content=[
                    {"type": "text", "text": "Listo."},
                    {"type": "tool_use", "id": "call-3", "name": "agenda", "input": {}},
                ],
            ),
        ],
    )

    body = _get(client).json()
    assert body["messages"] == [
        {"role": "user", "text": "Busca mi cita"},
        {"role": "bot", "text": "Listo."},
    ]
    assert body["message_count"] == 3


async def test_caps_to_last_100_but_reports_full_count(client, graph):
    total = 120
    await _seed(graph, [HumanMessage(content=f"m{i:03d}") for i in range(total)])

    body = _get(client).json()
    # El contrato expone como máximo los últimos 100 mensajes visibles.
    assert body["message_count"] == total
    assert len(body["messages"]) == 100
    # Se conservan los ÚLTIMOS 100, no los primeros.
    assert body["messages"][0] == {"role": "user", "text": "m020"}
    assert body["messages"][-1] == {"role": "user", "text": "m119"}


async def test_empty_thread_returns_empty_messages(client, graph):
    """Thread existente sin mensajes: 200 con listas vacías, nunca 500."""
    await _seed(graph, [])

    res = _get(client)
    assert res.status_code == 200
    body = res.json()
    assert body["messages"] == []
    assert body["message_count"] == 0
    assert body["last_message"] is None


async def test_thread_with_only_hidden_messages_returns_empty_list(client, graph):
    await _seed(graph, [SystemMessage(content="Prompt interno")])

    res = _get(client)
    assert res.status_code == 200
    body = res.json()
    assert body["messages"] == []
    assert body["message_count"] == 1


def test_missing_thread_returns_404_not_500(client):
    res = _get(client, thread_id="no-existe")
    assert res.status_code == 404
