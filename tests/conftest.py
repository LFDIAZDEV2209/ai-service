"""Fixtures compartidos.

Usamos `FakeMessagesListChatModel` de langchain-core para probar el flujo
completo del grafo sin necesidad de API keys.
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from tests.fakes import FakeToolAwareModel

# Config para tests: DATABASE_URL la consumen el motor SQLAlchemy (se crea a
# nivel módulo al importar `app.db.engine`) y las sesiones que resuelven los
# endpoints bajo TestClient. En CI, el workflow levanta un servicio Postgres
# con ESTAS mismas credenciales (no hay infra implícita); localmente apunta a
# una DB de test dedicada — nunca a la de desarrollo. `setdefault` no pisa una
# variable ya definida en el entorno.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://test:test@localhost:5432/test",
)
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")
os.environ.setdefault("AUTO_MIGRATE", "false")

SIMPLE_ANSWER = "¡Hola! Soy CoppAI. ¿En qué te ayudo?"


@pytest.hookimpl(optionalhook=True)
def pytest_asyncio_loop_factories(item):
    """pytest-asyncio 1.4 usa Runner/loop_factory, no la policy del proceso.

    Solo la integración psycopg de Vita en Windows necesita Selector. El resto
    de la suite y Linux conservan su loop por defecto.
    """
    if sys.platform == "win32" and item.path.name in {
        "test_vita_postgres.py",
        "test_vita_history_postgres.py",
    }:
        return {"vita_psycopg_selector": asyncio.SelectorEventLoop}
    # Si el hook está registrado, pytest-asyncio 1.4 exige mapping también
    # para los demás items; new_event_loop conserva su factory/policy normal.
    return {"default": asyncio.new_event_loop}


@pytest.fixture(autouse=True)
def history_unit_boundary(request, monkeypatch):
    """Los tests anteriores siguen sin BD; el módulo dedicado prueba el servicio real.

    Solo sustituye la nueva frontera durable en tests que ya simulaban DB/locks.
    Nunca se aplica a las regresiones de historial ni a PostgreSQL.
    """
    from app.history import service

    @asynccontextmanager
    async def idle_worker():
        yield

    if request.node.path.name != "test_vita_history_postgres.py":
        monkeypatch.setattr(service, "retention_worker", idle_worker)
    if request.node.path.name not in {
        "test_api.py",
        "test_threads_state.py",
        "test_voice_transcript.py",
        "test_vita_opt_in.py",
        "test_vita_voice.py",
        "test_voice.py",
        "test_conversation_recovery.py",
    }:
        return

    @asynccontextmanager
    async def unlocked(_):
        yield

    async def prepare(user_id, thread_id, saver, config):
        config["configurable"]["vita_history"] = {
            "user_id": user_id,
            "thread_id": thread_id,
            "revision": 0,
        }

    for name in ("get", "ensure_locked", "activity_locked", "before_turn_locked"):
        monkeypatch.setattr(service.history_service, name, AsyncMock(return_value=None))
    monkeypatch.setattr(service.history_service, "prepare_turn", prepare)
    monkeypatch.setattr(service, "thread_guard", unlocked)
    monkeypatch.setattr("app.api.routes.threads.thread_guard", unlocked)
    monkeypatch.setattr("app.voice.vita_session.thread_guard", unlocked)
    from langgraph.checkpoint.memory import MemorySaver

    monkeypatch.setattr(
        "app.voice.vita_session.get_checkpointer", AsyncMock(return_value=MemorySaver())
    )


@pytest.fixture
def simple_graph():
    """Grafo con LLM falso que responde siempre lo mismo (sin tools)."""
    from app.graph.graph import build_graph

    # Objetos AIMessage DISTINTOS: add_messages deduplica por id y el historial
    # acumulado sería colapsado si reusáramos el mismo objeto.
    fake = FakeToolAwareModel(responses=[AIMessage(content=SIMPLE_ANSWER) for _ in range(10)])
    return build_graph(model=fake, checkpointer=MemorySaver())
