"""Esquema de estado global del grafo supervisor (LangGraph)."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    """Estado compartido por todos los nodos del grafo supervisor.

    - `messages` usa el reducer `add_messages`: se acumula el historial y se
      deduplican mensajes por `id` (crucial para el checkpointer multi-turno).
    - `tools_used` usa `operator.add`: acumula tools ejecutadas en el turno.
    """

    # Mensaje crudo del usuario (entrada del grafo)
    input: str

    # Historial de conversación (reducer: append + dedupe)
    messages: Annotated[list[AnyMessage], add_messages]

    # Resultado de los guardrails de entrada: {safe, reason, pattern, sanitized}
    guardrail: dict[str, Any]

    # Tools ejecutadas en el turno (reducer: acumula)
    tools_used: Annotated[list[str], operator.add]

    # Fuentes RAG usadas en el turno (metadata para auditoría)
    rag_sources: list[str]

    # Clase del modelo que atendió el turno (telemetría)
    provider: str

    # Clave del perfil de agente activo (ver app/agents/registry.py)
    agent: str
