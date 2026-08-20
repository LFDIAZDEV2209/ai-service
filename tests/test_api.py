"""Smoke tests de la API (con el grafo sobreescrito con un LLM falso)."""

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from app.api.deps import get_db_session, get_graph
from app.api.main import create_app
from app.core.config import get_settings
from app.graph.graph import build_graph
from tests.fakes import FakeToolAwareModel

FAKE_ANSWER = "Respuesta de prueba"

# Los endpoints funcionales exigen X-Internal-Key (canal backend → AI).
INTERNAL_KEY = get_settings().internal_api_key
HEADERS = {"X-Internal-Key": INTERNAL_KEY}


class FakeDbSession:
    """Sesión de BD fake: los smoke tests no tocan Postgres.

    El tracker de observabilidad y los nodos de memoria usan la sesión; en
    los tests solo se verifica que el chat responde, no la persistencia.
    """

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
def client():
    app = create_app()
    fake = FakeToolAwareModel(
        responses=[AIMessage(content=FAKE_ANSWER) for _ in range(5)]
    )

    def override():
        return build_graph(model=fake, checkpointer=MemorySaver())

    app.dependency_overrides[get_graph] = override
    app.dependency_overrides[get_db_session] = FakeDbSession
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_health(client):
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["provider"] in {"anthropic", "openai"}
    assert "calculate" in body["tools"]


def test_chat_requires_internal_key(client):
    res = client.post("/api/v1/chat", json={"message": "hola"})
    assert res.status_code == 401


def test_chat_returns_answer(client):
    res = client.post("/api/v1/chat", json={"message": "hola"}, headers=HEADERS)
    assert res.status_code == 200
    body = res.json()
    assert body["thread_id"]
    assert body["answer"] == FAKE_ANSWER
    assert body["agent"] == "base"


def test_chat_stream_sse(client):
    with client.stream(
        "POST", "/api/v1/chat/stream", json={"message": "hola"}, headers=HEADERS
    ) as res:
        assert res.status_code == 200
        text = "".join(res.iter_text())
        assert "event:" in text
        assert "done" in text
