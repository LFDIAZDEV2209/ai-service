"""Descriptor del grafo de un agente para su visualización (flujos en vivo).

La topología del grafo supervisor es única (`app/graph/graph.py`); lo que
cambia por agente es la configuración efectiva (tools, RAG, memoria). Este
módulo traduce `AgentRuntimeConfig` a un descriptor nodos+aristas que el
frontend ERP dibuja y anima con los eventos `flow` del stream SSE.
"""

from __future__ import annotations

from typing import Any

from app.agents.runtime_config import AgentRuntimeConfig
from app.schemas.agent_graph import (
    AgentGraphConfig,
    AgentGraphEdge,
    AgentGraphMemoryConfig,
    AgentGraphNode,
    AgentGraphRagConfig,
    AgentGraphResponse,
)
from app.tools.registry import TOOLS_BY_NAME

# Etiqueta, tipo y descripción de cada nodo del grafo supervisor (es).
NODE_CATALOG: dict[str, tuple[str, str, str]] = {
    "start": ("Inicio", "start", "Entrada del turno de chat."),
    "guardrails": (
        "Guardrails",
        "guard",
        "Valida seguridad e inyección de prompt y sanitiza la entrada.",
    ),
    "memory_load": (
        "Memoria del usuario",
        "memory",
        "Recupera hechos persistidos del usuario para el prompt.",
    ),
    "experience_load": (
        "Experiencias previas",
        "memory",
        "Inyecta experiencias de turnos anteriores (adaptive memory).",
    ),
    "agent": (
        "Agente (LLM)",
        "llm",
        "Modelo con el prompt del agente: responde o solicita tools.",
    ),
    "tools": (
        "Herramientas",
        "tools",
        "Ejecuta las tools solicitadas por el agente.",
    ),
    "memory_save": (
        "Guardar memoria",
        "memory",
        "Extrae hechos nuevos del turno y los persiste.",
    ),
    "end": ("Fin", "end", "Cierre del turno."),
}

RAG_TOOL_NAME = "retrieve_knowledge"


def _node(node_id: str, meta: dict[str, Any] | None = None) -> AgentGraphNode:
    label, kind, description = NODE_CATALOG[node_id]
    return AgentGraphNode(
        id=node_id,
        label=label,
        kind=kind,  # type: ignore[arg-type]
        description=description,
        meta=meta or {},
    )


def effective_tool_names(config: AgentRuntimeConfig) -> list[str]:
    """Tools efectivas del agente: subconjunto configurado o todas por defecto.

    La tool de RAG (`retrieve_knowledge`) solo se agrega cuando el retrieval
    está habilitado Y hay KBs explícitas (mismo criterio que
    `AgentRuntimeRegistry._compile` — aislamiento entre agentes).
    """
    names = list(config.tools) if config.tools else sorted(TOOLS_BY_NAME)
    retrieval = config.retrieval_config
    if retrieval.enabled and retrieval.knowledge_base_ids and RAG_TOOL_NAME not in names:
        names.append(RAG_TOOL_NAME)
    return names


def build_graph_descriptor(
    *,
    agent_type_id: str,
    version_id: str | None = None,
    config: AgentRuntimeConfig | None = None,
) -> AgentGraphResponse:
    """Construye el descriptor nodos+aristas del grafo supervisor.

    Args:
        agent_type_id: id del tipo de agente.
        version_id: versión activa (solo con config de runtime).
        config: configuración sincronizada; None → descriptor del grafo base
            (memoria deshabilitada, todas las tools).
    """
    effective = config or AgentRuntimeConfig()
    memory_enabled = effective.memory_config.enabled
    tool_names = effective_tool_names(effective)
    retrieval = effective.retrieval_config
    rag_enabled = retrieval.enabled and bool(retrieval.knowledge_base_ids)
    memory_categories = list(effective.memory_config.categories)

    # Meta por nodo (snake_case): detalle que muestra el drawer del playground.
    llm_meta: dict[str, Any] = {}
    if effective.provider:
        llm_meta["provider"] = effective.provider
    if effective.model:
        llm_meta["model"] = effective.model
    if effective.temperature is not None:
        llm_meta["temperature"] = effective.temperature
    if effective.max_tokens is not None:
        llm_meta["max_tokens"] = effective.max_tokens
    llm_meta["max_tool_calls"] = effective.max_tool_calls
    llm_meta["recursion_limit"] = effective.recursion_limit
    if rag_enabled:
        llm_meta["rag_enabled"] = True
        llm_meta["knowledge_base_count"] = len(retrieval.knowledge_base_ids)
        llm_meta["top_k"] = retrieval.top_k
    if memory_enabled:
        llm_meta["memory_enabled"] = True
    tools_meta: dict[str, Any] = {"tools": tool_names, "max_tool_calls": effective.max_tool_calls}
    memory_meta: dict[str, Any] = {"categories": memory_categories} if memory_enabled else {}

    nodes: list[AgentGraphNode] = [_node("start"), _node("guardrails")]
    if memory_enabled:
        nodes.append(_node("memory_load", memory_meta))
        nodes.append(_node("experience_load", memory_meta))
    nodes.append(_node("agent", llm_meta))
    if tool_names:
        nodes.append(_node("tools", tools_meta))
    if memory_enabled:
        nodes.append(_node("memory_save", memory_meta))
    nodes.append(_node("end"))

    edges: list[AgentGraphEdge] = [
        AgentGraphEdge(source="start", target="guardrails"),
        AgentGraphEdge(
            source="guardrails",
            target="memory_load" if memory_enabled else "agent",
            kind="conditional",
            label="seguro",
        ),
        AgentGraphEdge(
            source="guardrails",
            target="end",
            kind="conditional",
            label="inseguro",
        ),
    ]
    if memory_enabled:
        edges.append(AgentGraphEdge(source="memory_load", target="experience_load"))
        edges.append(AgentGraphEdge(source="experience_load", target="agent"))

    edges.extend(
        [
            AgentGraphEdge(
                source="agent",
                target="tools",
                kind="conditional",
                label="tool_calls",
            ),
            AgentGraphEdge(
                source="agent",
                target="memory_save" if memory_enabled else "end",
                kind="conditional",
                label="respuesta",
            ),
        ]
    )
    if tool_names:
        edges.append(AgentGraphEdge(source="tools", target="agent"))
    if memory_enabled:
        edges.append(AgentGraphEdge(source="memory_save", target="end"))

    graph_config = AgentGraphConfig(
        provider=effective.provider,
        model=effective.model,
        temperature=effective.temperature,
        max_tokens=effective.max_tokens,
        tools=tool_names,
        rag=AgentGraphRagConfig(
            enabled=rag_enabled,
            knowledge_base_count=len(retrieval.knowledge_base_ids),
            top_k=retrieval.top_k,
        ),
        memory=AgentGraphMemoryConfig(
            enabled=memory_enabled,
            categories=memory_categories,
        ),
        max_tool_calls=effective.max_tool_calls,
        recursion_limit=effective.recursion_limit,
    )

    return AgentGraphResponse(
        agent_type_id=agent_type_id,
        source="runtime" if config is not None else "base",
        version_id=version_id,
        nodes=nodes,
        edges=edges,
        config=graph_config,
    )
