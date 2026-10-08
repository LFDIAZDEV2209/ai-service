"""Tests del runtime multi-agente (registry + configuración).

No se necesitan API keys: se inyecta una fábrica de modelos falso y el
acceso a la BD se mockea con una sesión asíncrona fake.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from app.agents.runtime_config import AgentRuntimeConfig
from app.agents.runtime_registry import AgentRuntimeError, AgentRuntimeRegistry
from app.tools.retrieval import make_retrieve_tool
from tests.fakes import FakeToolAwareModel

AGENT_ID = "11111111-1111-1111-1111-111111111111"
VERSION_ID = "22222222-2222-2222-2222-222222222222"


def make_fake_model_factory(**overrides):
    def factory(**kwargs):
        return FakeToolAwareModel(responses=[AIMessage(content="respuesta") for _ in range(5)])

    return factory


def make_registry(**overrides):
    """Registry con MemorySaver inyectado (los tests no tocan Postgres)."""
    return AgentRuntimeRegistry(
        model_factory=make_fake_model_factory(),
        checkpointer_factory=lambda: MemorySaver(),
        **overrides,
    )


def make_session_with_row(config: dict, knowledge_bases: list[dict] | None = None) -> AsyncMock:
    """Sesión asíncrona fake cuya consulta devuelve una fila de runtime config."""
    row = SimpleNamespace(
        agent_type_id=AGENT_ID,
        version_id=VERSION_ID,
        is_active=True,
        config=config,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    session = AsyncMock()
    knowledge_result = MagicMock()
    knowledge_result.mappings.return_value.all.return_value = (
        knowledge_bases
        if knowledge_bases is not None
        else [
            {"id": kb, "scope": "Agent"}
            for kb in config.get("retrieval_config", {}).get("knowledge_base_ids", [])
        ]
    )

    async def execute(statement, *_args, **_kwargs):
        return knowledge_result if "agents.knowledge_bases" in str(statement) else result

    session.execute.side_effect = execute
    return session


# ── AgentRuntimeConfig ──────────────────────────────────────────────────────


def test_runtime_config_defaults():
    config = AgentRuntimeConfig.model_validate({})
    assert config.system_prompt == "Eres un asistente útil de CoppAddresd."
    assert config.tools == []
    assert config.retrieval_config.enabled is False
    assert config.recursion_limit == 25
    assert config.effective_system_prompt() == config.system_prompt


def test_runtime_config_prompt_compuesto():
    config = AgentRuntimeConfig.model_validate({"system_prompt": "base", "prompt": "extra"})
    assert config.effective_system_prompt() == "base\n\nextra"


def test_runtime_config_overrides():
    config = AgentRuntimeConfig.model_validate(
        {
            "provider": "openai",
            "model": "gpt-4o",
            "temperature": 0.5,
            "tools": ["get_time"],
            "recursion_limit": 40,
        }
    )
    assert config.provider == "openai"
    assert config.tools == ["get_time"]
    assert config.recursion_limit == 40


# ── Registry ────────────────────────────────────────────────────────────────


async def test_get_agent_compila_y_cachea():
    session = make_session_with_row({"system_prompt": "Eres nutricionista"})
    registry = make_registry()

    first = await registry.get_agent(AGENT_ID, session)
    second = await registry.get_agent(AGENT_ID, session)

    assert first.graph is second.graph  # cacheado: mismo objeto
    assert first.agent_type_id == AGENT_ID
    assert first.version_id == VERSION_ID
    assert first.runtime_config.system_prompt == "Eres nutricionista"


async def test_invalidate_recompila():
    session = make_session_with_row({"system_prompt": "v1"})
    registry = make_registry()

    first = await registry.get_agent(AGENT_ID, session)
    registry.invalidate(AGENT_ID)
    second = await registry.get_agent(AGENT_ID, session)

    assert first.graph is not second.graph  # recompilado tras invalidar


async def test_get_agent_sin_fila_levanta_error():
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = AsyncMock()
    session.execute.return_value = result
    registry = make_registry()

    with pytest.raises(AgentRuntimeError, match="no sincronizado"):
        await registry.get_agent(AGENT_ID, session)


async def test_tool_desconocida_levanta_error():
    session = make_session_with_row({"tools": ["tool_que_no_existe"]})
    registry = make_registry()

    with pytest.raises(AgentRuntimeError, match="tool_que_no_existe"):
        await registry.get_agent(AGENT_ID, session)


def test_registry_clear():
    registry = make_registry()
    registry._cache["x"] = object()
    registry.clear()
    assert registry._cache == {}


async def test_retrieval_enabled_injects_tool():
    """Con `retrieval_config.enabled`, el grafo compilado incluye la tool de RAG."""
    session = make_session_with_row(
        {
            "system_prompt": "Eres doctor",
            "retrieval_config": {"enabled": True, "knowledge_base_ids": ["kb-1"], "top_k": 3},
        }
    )
    registry = make_registry()
    compiled = await registry.get_agent(AGENT_ID, session)

    # La tool se enlaza al modelo vía bind_tools; verificamos que se compiló sin
    # error y que la config de retrieval se preservó en el runtime config.
    assert compiled.runtime_config.retrieval_config.enabled is True
    assert compiled.runtime_config.retrieval_config.knowledge_base_ids == ["kb-1"]
    assert compiled.graph is not None


async def test_retrieval_disabled_no_tool():
    """Sin retrieval habilitado, la compilación no exige KBs configuradas."""
    session = make_session_with_row(
        {
            "system_prompt": "Eres base",
            "retrieval_config": {"enabled": False},
        }
    )
    registry = make_registry()
    compiled = await registry.get_agent(AGENT_ID, session)
    assert compiled.runtime_config.retrieval_config.enabled is False


async def test_retrieval_empty_selection_resolves_active_global_and_owned_bases():
    session = make_session_with_row(
        {"retrieval_config": {"enabled": True, "knowledge_base_ids": []}},
        knowledge_bases=[{"id": "global", "scope": "Global"}, {"id": "owned", "scope": "Agent"}],
    )
    compiled = await make_registry().get_agent(AGENT_ID, session)
    assert compiled.runtime_config.retrieval_config.knowledge_base_ids == ["global", "owned"]
    statement, params = session.execute.call_args.args
    assert "status = 'Activo'" in str(statement)
    assert "agent_type_id::text = :agent_type_id" in str(statement)
    assert params == {"agent_type_id": AGENT_ID}


async def test_retrieval_explicit_selection_keeps_globals_and_excludes_unassigned_ids():
    session = make_session_with_row(
        {
            "retrieval_config": {
                "enabled": True,
                "knowledge_base_ids": ["chosen", "foreign", "inactive"],
            }
        },
        knowledge_bases=[
            {"id": "global", "scope": "Global"},
            {"id": "chosen", "scope": "Agent"},
            {"id": "other-owned", "scope": "Agent"},
        ],
    )
    compiled = await make_registry().get_agent(AGENT_ID, session)
    assert compiled.runtime_config.retrieval_config.knowledge_base_ids == ["chosen", "global"]


async def test_retrieval_cached_graph_refreshes_when_catalog_changes():
    bases = [{"id": "global", "scope": "Global"}]
    session = make_session_with_row({"retrieval_config": {"enabled": True}}, knowledge_bases=bases)
    registry = make_registry()
    first = await registry.get_agent(AGENT_ID, session)
    assert (await registry.get_agent(AGENT_ID, session)).graph is first.graph
    bases.clear()
    second = await registry.get_agent(AGENT_ID, session)
    assert second.graph is not first.graph
    assert second.runtime_config.retrieval_config.knowledge_base_ids == []
    # Reaparecer una global también recompila; la configuración vacía original se conserva.
    bases.append({"id": "new-global", "scope": "Global"})
    third = await registry.get_agent(AGENT_ID, session)
    assert third.runtime_config.retrieval_config.knowledge_base_ids == ["new-global"]


async def test_resolved_knowledge_bases_bind_filtered_retrieval_tool():
    session = make_session_with_row(
        {"retrieval_config": {"enabled": True, "knowledge_base_ids": [], "top_k": 4}},
        knowledge_bases=[{"id": "global", "scope": "Global"}],
    )
    with patch(
        "app.agents.runtime_registry.make_retrieve_tool",
        wraps=make_retrieve_tool,
    ) as make_tool:
        compiled = await make_registry().get_agent(AGENT_ID, session)
        make_tool.assert_called_once_with(knowledge_base_ids=["global"], top_k=4)
        result = await compiled.graph.ainvoke(
            {"input": "Hola", "messages": [], "tools_used": [], "suggestions": []},
            config={"configurable": {"thread_id": "scope-test"}},
        )
        assert result["messages"][-1].content == "respuesta"
