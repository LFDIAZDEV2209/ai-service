"""Fixtures compartidos.

Usamos `FakeMessagesListChatModel` de langchain-core para probar el flujo
completo del grafo sin necesidad de API keys.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from app.graph.graph import build_graph
from tests.fakes import FakeToolAwareModel

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
