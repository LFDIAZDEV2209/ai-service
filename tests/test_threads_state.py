"""Tests del historial paginado expuesto por GET /threads/{thread_id}/state.

La app del paciente pide páginas de mensajes visibles (usuario/bot) desde la
más reciente hacia atrás: `limit` es el tamaño de página, `before` el cursor
de mensajes visibles a saltear desde el más nuevo, y la respuesta incluye
`has_more`/`next_cursor` para encadenar la página anterior. `message_count`
sigue reportando el total real (incluidos los mensajes internos) y
`last_message` conserva el comportamiento previo del endpoint.
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


def _get(
    client,
    thread_id: str = THREAD_ID,
    *,
    limit: int | None = None,
    before: int | None = None,
):
    params: dict[str, object] = {"user_id": USER_ID}
    if limit is not None:
        params["limit"] = limit
    if before is not None:
        params["before"] = before
    return client.get(
        URL.format(thread_id=thread_id),
        params=params,
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


async def test_limit_100_returns_last_100_and_reports_full_count(client, graph):
    total = 120
    await _seed(graph, [HumanMessage(content=f"m{i:03d}") for i in range(total)])

    body = _get(client, limit=100).json()
    # Con limit=100 (tope) se expone como máximo los últimos 100 visibles.
    assert body["message_count"] == total
    assert len(body["messages"]) == 100
    # Se conservan los ÚLTIMOS 100, no los primeros.
    assert body["messages"][0] == {"role": "user", "text": "m020"}
    assert body["messages"][-1] == {"role": "user", "text": "m119"}
    # Quedan 20 visibles más antiguos: se indica la página anterior.
    assert body["has_more"] is True
    assert body["next_cursor"] == 100


async def test_default_page_returns_newest_10(client, graph):
    await _seed(graph, [HumanMessage(content=f"m{i:02d}") for i in range(25)])

    body = _get(client).json()
    # Sin parámetros: limit=10 desde el extremo más nuevo (m24..m15).
    assert [m["text"] for m in body["messages"]] == [f"m{i:02d}" for i in range(15, 25)]
    assert body["message_count"] == 25
    assert body["has_more"] is True
    assert body["next_cursor"] == 10


async def test_before_pages_backwards_until_last_page(client, graph):
    await _seed(graph, [HumanMessage(content=f"m{i:02d}") for i in range(25)])

    # Segunda página: before=10 ⇒ visible[5:15] (m05..m14).
    second = _get(client, before=10).json()
    assert [m["text"] for m in second["messages"]] == [f"m{i:02d}" for i in range(5, 15)]
    assert second["message_count"] == 25
    assert second["has_more"] is True
    assert second["next_cursor"] == 20

    # Tercera página (última): before=20 ⇒ visible[0:5] (m00..m04).
    last = _get(client, before=20).json()
    assert [m["text"] for m in last["messages"]] == [f"m{i:02d}" for i in range(0, 5)]
    assert last["message_count"] == 25
    assert last["has_more"] is False
    assert last["next_cursor"] is None


async def test_single_page_covering_all_has_no_more(client, graph):
    await _seed(graph, [HumanMessage(content=f"m{i:02d}") for i in range(25)])

    # limit mayor que el total visible: toda la historia en una sola página.
    body = _get(client, limit=30).json()
    assert len(body["messages"]) == 25
    assert body["has_more"] is False
    assert body["next_cursor"] is None


def test_limit_above_ceiling_returns_422(client):
    # El tope duro de página se mantiene: limit=101 es inválido.
    assert _get(client, limit=101).status_code == 422


async def test_empty_thread_returns_empty_messages(client, graph):
    """Thread existente sin mensajes: 200 con listas vacías, nunca 500."""
    await _seed(graph, [])

    res = _get(client)
    assert res.status_code == 200
    body = res.json()
    assert body["messages"] == []
    assert body["message_count"] == 0
    assert body["last_message"] is None
    assert body["has_more"] is False
    assert body["next_cursor"] is None


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
