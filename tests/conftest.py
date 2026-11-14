"""Fixtures compartidos.

Usamos `FakeMessagesListChatModel` de langchain-core para probar el flujo
completo del grafo sin necesidad de API keys.
"""

from __future__ import annotations

import asyncio
import os
import sys

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
    if sys.platform == "win32" and item.path.name == "test_vita_postgres.py":
        return {"vita_psycopg_selector": asyncio.SelectorEventLoop}
    # Si el hook está registrado, pytest-asyncio 1.4 exige mapping también
    # para los demás items; new_event_loop conserva su factory/policy normal.
    return {"default": asyncio.new_event_loop}


@pytest.fixture
def simple_graph():
    """Grafo con LLM falso que responde siempre lo mismo (sin tools)."""
    from app.graph.graph import build_graph

    # Objetos AIMessage DISTINTOS: add_messages deduplica por id y el historial
    # acumulado sería colapsado si reusáramos el mismo objeto.
    fake = FakeToolAwareModel(responses=[AIMessage(content=SIMPLE_ANSWER) for _ in range(10)])
    return build_graph(model=fake, checkpointer=MemorySaver())
