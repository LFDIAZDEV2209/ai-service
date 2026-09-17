"""Esquemas del descriptor de grafo de un agente (visualización de flujos).

El frontend ERP consume `GET /api/v1/agents/{id}/graph` (proxy del backend)
para dibujar el flujo del agente: nodos, aristas y la configuración efectiva
con la que se ejecuta (modelo, tools, RAG, memoria).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

NodeKind = Literal["start", "guard", "llm", "tools", "memory", "end"]
EdgeKind = Literal["flow", "conditional"]
GraphSource = Literal["runtime", "base"]


class AgentGraphNode(BaseModel):
    """Nodo del grafo mostrado en el diagrama."""

    id: str
    label: str
    kind: NodeKind
    description: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class AgentGraphEdge(BaseModel):
    """Arista entre dos nodos (flujo directo o condicional)."""

    source: str
    target: str
    kind: EdgeKind = "flow"
    label: str | None = None


class AgentGraphRagConfig(BaseModel):
    enabled: bool = False
    knowledge_base_count: int = 0
    top_k: int = 5


class AgentGraphMemoryConfig(BaseModel):
    enabled: bool = False
    categories: list[str] = Field(default_factory=list)


class AgentGraphConfig(BaseModel):
    """Configuración efectiva con la que corre el agente."""

    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    tools: list[str] = Field(default_factory=list)
    rag: AgentGraphRagConfig = Field(default_factory=AgentGraphRagConfig)
    memory: AgentGraphMemoryConfig = Field(default_factory=AgentGraphMemoryConfig)
    max_tool_calls: int = 8
    recursion_limit: int = 25


class AgentGraphResponse(BaseModel):
    """Descriptor completo del grafo de un tipo de agente."""

    agent_type_id: str
    source: GraphSource
    version_id: str | None = None
    nodes: list[AgentGraphNode]
    edges: list[AgentGraphEdge]
    config: AgentGraphConfig
