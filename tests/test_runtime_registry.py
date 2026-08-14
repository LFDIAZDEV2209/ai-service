"""Tests del runtime multi-agente (registry + configuración).

No se necesitan API keys: se inyecta una fábrica de modelos falso y el
acceso a la BD se mockea con una sesión asíncrona fake.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage

from app.agents.runtime_config import AgentRuntimeConfig
from app.agents.runtime_registry import AgentRuntimeError, AgentRuntimeRegistry
from tests.fakes import FakeToolAwareModel

AGENT_ID = "11111111-1111-1111-1111-111111111111"
VERSION_ID = "22222222-2222-2222-2222-222222222222"


def make_fake_model_factory(**overrides):
    def factory(**kwargs):
        return FakeToolAwareModel(
            responses=[AIMessage(content="respuesta") for _ in range(5)]
        )

    return factory


def make_session_with_row(config: dict) -> AsyncMock:
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
    session.execute.return_value = result
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
    config = AgentRuntimeConfig.model_validate(
        {"system_prompt": "base", "prompt": "extra"}
    )
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
    registry = AgentRuntimeRegistry(model_factory=make_fake_model_factory())

    first = await registry.get_agent(AGENT_ID, session)
    second = await registry.get_agent(AGENT_ID, session)

    assert first.graph is second.graph  # cacheado: mismo objeto
    assert first.agent_type_id == AGENT_ID
    assert first.version_id == VERSION_ID
    assert first.runtime_config.system_prompt == "Eres nutricionista"


async def test_invalidate_recompila():
    session = make_session_with_row({"system_prompt": "v1"})
    registry = AgentRuntimeRegistry(model_factory=make_fake_model_factory())

    first = await registry.get_agent(AGENT_ID, session)
    registry.invalidate(AGENT_ID)
    second = await registry.get_agent(AGENT_ID, session)

    assert first.graph is not second.graph  # recompilado tras invalidar


async def test_get_agent_sin_fila_levanta_error():
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = AsyncMock()
    session.execute.return_value = result
    registry = AgentRuntimeRegistry(model_factory=make_fake_model_factory())

    with pytest.raises(AgentRuntimeError, match="no sincronizado"):
        await registry.get_agent(AGENT_ID, session)


async def test_tool_desconocida_levanta_error():
    session = make_session_with_row({"tools": ["tool_que_no_existe"]})
    registry = AgentRuntimeRegistry(model_factory=make_fake_model_factory())

    with pytest.raises(AgentRuntimeError, match="tool_que_no_existe"):
        await registry.get_agent(AGENT_ID, session)


def test_registry_clear():
    registry = AgentRuntimeRegistry()
    registry._cache["x"] = object()
    registry.clear()
    assert registry._cache == {}
