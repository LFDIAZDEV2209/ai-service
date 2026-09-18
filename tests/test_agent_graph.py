"""Tests del descriptor de grafo y de los eventos `flow` del stream SSE.

Cubren:
- `build_graph_descriptor`: topología base vs runtime (memoria, RAG, tools).
- `GET /internal/agents/{id}/graph`: auth interna y fallback al grafo base.
- `POST /api/v1/chat/stream`: eventos `flow` start/end por nodo con duración.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from app.agents.graph_descriptor import build_graph_descriptor
from app.agents.runtime_config import AgentRuntimeConfig, MemoryConfig, RetrievalConfig
from app.api.deps import get_db_session, get_graph
from app.api.main import create_app
from app.core.config import get_settings
from app.graph.graph import build_graph
from tests.fakes import FakeToolAwareModel

INTERNAL_KEY = get_settings().internal_api_key
HEADERS = {"X-Internal-Key": INTERNAL_KEY}


# --- Descriptor (unit) -------------------------------------------------------


def _node_ids(graph) -> list[str]:
    return [node.id for node in graph.nodes]


def test_descriptor_base_sin_memoria_ni_kbs():
    """Sin config sincronizada: grafo base — sin nodos de memoria, todas las tools."""
    graph = build_graph_descriptor(agent_type_id="agente-1")

    assert graph.source == "base"
    assert graph.version_id is None
    assert _node_ids(graph) == ["start", "guardrails", "agent", "tools", "end"]
    assert graph.config.memory.enabled is False
    assert graph.config.rag.enabled is False
    assert "calculate" in graph.config.tools
    assert "retrieve_knowledge" not in graph.config.tools

    conditional = [edge for edge in graph.edges if edge.kind == "conditional"]
    assert {(edge.source, edge.target) for edge in conditional} >= {
        ("guardrails", "agent"),
        ("guardrails", "end"),
        ("agent", "tools"),
        ("agent", "end"),
    }


def test_descriptor_runtime_con_memoria_rag_y_tools():
    """Config sincronizada: nodos de memoria, tool RAG y resumen de config efectiva."""
    config = AgentRuntimeConfig(
        provider="anthropic",
        model="claude-sonnet-4",
        temperature=0.3,
        tools=["calculate"],
        retrieval_config=RetrievalConfig(
            enabled=True, knowledge_base_ids=["kb-1", "kb-2"], top_k=4
        ),
        memory_config=MemoryConfig(enabled=True, categories=["clinico", "preferencias"]),
        max_tool_calls=5,
        recursion_limit=18,
    )
    graph = build_graph_descriptor(
        agent_type_id="agente-2", version_id="v-9", config=config
    )

    assert graph.source == "runtime"
    assert graph.version_id == "v-9"
    assert _node_ids(graph) == [
        "start",
        "guardrails",
        "memory_load",
        "experience_load",
        "agent",
        "tools",
        "memory_save",
        "end",
    ]
    assert graph.config.provider == "anthropic"
    assert graph.config.model == "claude-sonnet-4"
    assert graph.config.temperature == 0.3
    assert graph.config.tools == ["calculate", "retrieve_knowledge"]
    assert graph.config.rag.enabled is True
    assert graph.config.rag.knowledge_base_count == 2
    assert graph.config.rag.top_k == 4
    assert graph.config.memory.enabled is True
    assert graph.config.memory.categories == ["clinico", "preferencias"]
    assert graph.config.max_tool_calls == 5
    assert graph.config.recursion_limit == 18

    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("memory_load", "experience_load") in edges
    assert ("experience_load", "agent") in edges
    assert ("memory_save", "end") in edges
    assert ("tools", "agent") in edges


def test_descriptor_meta_por_nodo_para_drawer_playground():
    """El meta por nodo expone el detalle de configuración (drawer del playground)."""
    config = AgentRuntimeConfig(
        provider="anthropic",
        model="claude-sonnet-4",
        temperature=0.3,
        max_tokens=2048,
        tools=["calculate"],
        retrieval_config=RetrievalConfig(
            enabled=True, knowledge_base_ids=["kb-1", "kb-2"], top_k=4
        ),
        memory_config=MemoryConfig(enabled=True, categories=["clinico", "preferencias"]),
        max_tool_calls=5,
        recursion_limit=18,
    )
    graph = build_graph_descriptor(
        agent_type_id="agente-4", version_id="v-1", config=config
    )

    by_id = {node.id: node for node in graph.nodes}
    agent_meta = by_id["agent"].meta
    assert agent_meta["provider"] == "anthropic"
    assert agent_meta["model"] == "claude-sonnet-4"
    assert agent_meta["temperature"] == 0.3
    assert agent_meta["max_tokens"] == 2048
    assert agent_meta["max_tool_calls"] == 5
    assert agent_meta["recursion_limit"] == 18
    assert agent_meta["rag_enabled"] is True
    assert agent_meta["knowledge_base_count"] == 2
    assert agent_meta["top_k"] == 4
    assert agent_meta["memory_enabled"] is True

    tools_meta = by_id["tools"].meta
    assert tools_meta["tools"] == ["calculate", "retrieve_knowledge"]
    assert tools_meta["max_tool_calls"] == 5

    for node_id in ("memory_load", "experience_load", "memory_save"):
        assert by_id[node_id].meta["categories"] == ["clinico", "preferencias"]

    # Nodos fijos sin meta.
    assert by_id["start"].meta == {}
    assert by_id["end"].meta == {}
    assert by_id["guardrails"].meta == {}


def test_descriptor_retrieval_habilitado_sin_kbs_no_agrega_tool_rag():
    """`enabled` sin KBs explícitas no agrega la tool (aislamiento entre agentes)."""
    config = AgentRuntimeConfig(
        retrieval_config=RetrievalConfig(enabled=True, knowledge_base_ids=[]),
    )
    graph = build_graph_descriptor(agent_type_id="agente-3", config=config)

    assert "retrieve_knowledge" not in graph.config.tools
    assert graph.config.rag.enabled is False


# --- API ---------------------------------------------------------------------


class _EmptyScalarResult:
    def scalar_one_or_none(self):
        return None


class FakeDbSession:
    """Sesión fake: los tests de endpoint no tocan Postgres."""

    async def add(self, *args, **kwargs):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def get(self, *args, **kwargs):
        return None

    async def execute(self, *args, **kwargs):
        return _EmptyScalarResult()


@pytest.fixture
def client():
    app = create_app()
    fake = FakeToolAwareModel(responses=[AIMessage(content="Hola") for _ in range(5)])

    def override():
        return build_graph(model=fake, checkpointer=MemorySaver())

    app.dependency_overrides[get_graph] = override
    app.dependency_overrides[get_db_session] = FakeDbSession
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_graph_endpoint_requiere_internal_key(client):
    res = client.get("/internal/agents/agente-1/graph")
    assert res.status_code == 401


def test_graph_endpoint_agente_no_sincronizado_devuelve_base(client):
    res = client.get("/internal/agents/agente-1/graph", headers=HEADERS)

    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "base"
    assert body["agent_type_id"] == "agente-1"
    assert [node["id"] for node in body["nodes"]] == [
        "start",
        "guardrails",
        "agent",
        "tools",
        "end",
    ]
    assert body["edges"]


def _sse_blocks(text: str) -> list[tuple[str, dict | None]]:
    """Parsea el stream SSE en (evento, data JSON) — data puede ser None."""
    blocks: list[tuple[str, dict | None]] = []
    for raw in text.split("\n\n"):
        event = None
        data: dict | None = None
        for line in raw.splitlines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                try:
                    data = json.loads(line[len("data: ") :])
                except json.JSONDecodeError:
                    data = None
        if event:
            blocks.append((event, data))
    return blocks


def test_stream_emite_eventos_flow_start_y_end(client):
    """El stream incluye eventos `flow` con fase start/end y duración por nodo."""
    with client.stream(
        "POST", "/api/v1/chat/stream", json={"message": "hola"}, headers=HEADERS
    ) as res:
        assert res.status_code == 200
        blocks = _sse_blocks("".join(res.iter_text()))

    flow = [data for event, data in blocks if event == "flow" and data is not None]
    assert flow, "el stream debe emitir eventos flow"

    starts = [evt for evt in flow if evt["phase"] == "start"]
    ends = [evt for evt in flow if evt["phase"] == "end"]
    assert {evt["node"] for evt in starts} >= {"guardrails", "agent"}
    assert {evt["node"] for evt in ends} >= {"guardrails", "agent"}

    # start/end se emparejan por (nodo, step) y el fin trae duración.
    for end in ends:
        assert end["step"] >= 1
        assert end["duration_ms"] >= 0
        matching = [
            evt
            for evt in starts
            if evt["node"] == end["node"] and evt["step"] == end["step"]
        ]
        assert matching, f"sin start para {end['node']} step {end['step']}"

    # El contrato previo sigue intacto.
    assert any(event == "done" for event, _ in blocks)
    token_nodes = {
        data.get("node")
        for event, data in blocks
        if event == "message" and data and data.get("type") == "token"
    }
    assert "agent" in token_nodes
