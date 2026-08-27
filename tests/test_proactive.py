"""Tests del endpoint interno de mensajes proactivos (inyección sin LLM).

Verifica que:
1. El endpoint exige `X-Internal-Key` (401 sin header).
2. Con `FakeToolAwareModel` + `MemorySaver`, el AIMessage queda en el estado
   del thread (persistido por el checkpointer, aislado por user_id).
3. Devuelve el `thread_id` (y el id del mensaje inyectado).
4. NO se llama al LLM: el fake se instancia con CERO respuestas — si el
   endpoint invocara `graph.ainvoke`, el fake lanzaría AssertionError.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from app.api.deps import get_db_session, get_graph
from app.api.main import create_app
from app.api.routes.proactive import (
    ProactiveMessageRequest,
    proactive_message,
)
from app.core.config import get_settings
from app.graph.graph import build_graph
from tests.fakes import FakeToolAwareModel

URL = "/internal/agents/proactive-message"

INTERNAL_KEY = get_settings().internal_api_key
HEADERS = {"X-Internal-Key": INTERNAL_KEY}


class FakeDbSession:
    """Sesión de BD fake: los tests de API no tocan Postgres."""

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
    """Grafo con LLM falso SIN respuestas: si se invoca, falla ruidosamente."""
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


def _storage_thread_id(user_id: str, thread_id: str) -> str:
    """Misma clave interna que usa `/chat` (aislamiento por user_id)."""
    return f"{user_id}::{thread_id}"


# --- API: autenticación y contrato --------------------------------------


def test_proactive_requires_internal_key(client):
    res = client.post(URL, json={"user_id": "u1", "message": "hola"})
    assert res.status_code == 401


def test_proactive_returns_thread_id_and_message_id(client):
    res = client.post(
        URL,
        json={"user_id": "user-1", "message": "¡Hola! ¿Cómo estás?"},
        headers=HEADERS,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["thread_id"] == "proactive-user-1"
    assert body["message_id"]


def test_proactive_uses_explicit_thread_id(client):
    res = client.post(
        URL,
        json={
            "user_id": "user-1",
            "message": "Mensaje en thread explícito",
            "thread_id": "thread-abc",
        },
        headers=HEADERS,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["thread_id"] == "thread-abc"


# --- Lógica: inyección en el estado sin LLM ------------------------------


async def test_injects_aimessage_into_thread_state_without_llm(graph):
    payload = ProactiveMessageRequest(
        user_id="user-1",
        message="¡Hola! Soy CoppAI, ¿en qué te ayudo?",
        thread_id="thread-abc",
    )

    resp = await proactive_message(payload, graph=graph)
    assert resp.thread_id == "thread-abc"
    assert resp.message_id

    # El mensaje quedó en el estado del thread (clave aislada por user_id).
    state = await graph.aget_state(
        {"configurable": {"thread_id": _storage_thread_id("user-1", "thread-abc")}}
    )
    messages = (state.values or {}).get("messages", [])
    assert len(messages) == 1
    last = messages[-1]
    assert isinstance(last, AIMessage)
    assert last.content == "¡Hola! Soy CoppAI, ¿en qué te ayudo?"
    assert last.id == resp.message_id


async def test_stable_thread_when_thread_id_omitted(graph):
    payload = ProactiveMessageRequest(user_id="user-1", message="Hola")

    resp = await proactive_message(payload, graph=graph)
    assert resp.thread_id == "proactive-user-1"

    state = await graph.aget_state(
        {"configurable": {"thread_id": _storage_thread_id("user-1", "proactive-user-1")}}
    )
    messages = (state.values or {}).get("messages", [])
    assert len(messages) == 1


async def test_injection_accumulates_messages_via_add_messages(graph):
    """Dos inyecciones al mismo thread se acumulan (reducer add_messages)."""
    for i in range(2):
        resp = await proactive_message(
            ProactiveMessageRequest(user_id="user-1", message=f"Mensaje {i}"),
            graph=graph,
        )
        assert resp.message_id

    state = await graph.aget_state(
        {"configurable": {"thread_id": _storage_thread_id("user-1", "proactive-user-1")}}
    )
    messages = (state.values or {}).get("messages", [])
    assert len(messages) == 2
    assert [m.content for m in messages] == ["Mensaje 0", "Mensaje 1"]


async def test_no_llm_called_when_fake_has_no_responses(graph):
    """El fake con 0 respuestas no debería fallar: solo se usa `aupdate_state`.

    Si el endpoint llamara a `graph.ainvoke`, el fake lanzaría
    "se quedó sin respuestas" (AssertionError) y este test fallaría.
    """
    resp = await proactive_message(
        ProactiveMessageRequest(user_id="user-1", message="Sin LLM"),
        graph=graph,
    )
    assert resp.message_id

    state = await graph.aget_state(
        {"configurable": {"thread_id": _storage_thread_id("user-1", "proactive-user-1")}}
    )
    messages = (state.values or {}).get("messages", [])
    assert len(messages) == 1
    assert messages[-1].content == "Sin LLM"
