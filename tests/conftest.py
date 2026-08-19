"""Fixtures compartidos.

Usamos `FakeMessagesListChatModel` de langchain-core para probar el flujo
completo del grafo sin necesidad de API keys.
"""

from __future__ import annotations

import os

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from app.graph.graph import build_graph
from tests.fakes import FakeToolAwareModel

# Config para tests: DATABASE_URL solo se usa para crear el motor SQLAlchemy
# (lazy, no conecta) — los tests nunca tocan Postgres (sesiones/checkpointer
# fake). `setdefault` no pisa una variable ya definida en el entorno.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://test:test@localhost:5432/test",
)
os.environ.setdefault("ENVIRONMENT", "test")

SIMPLE_ANSWER = "¡Hola! Soy CoppAI. ¿En qué te ayudo?"


@pytest.fixture
def simple_graph():
    """Grafo con LLM falso que responde siempre lo mismo (sin tools)."""
    # Objetos AIMessage DISTINTOS: add_messages deduplica por id y el historial
    # acumulado sería colapsado si reusáramos el mismo objeto.
    fake = FakeToolAwareModel(
        responses=[AIMessage(content=SIMPLE_ANSWER) for _ in range(10)]
    )
    return build_graph(model=fake, checkpointer=MemorySaver())
