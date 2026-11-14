"""RAG de perfiles: alcance resuelto, guard y fallback sin servicios externos."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.checkpoint.memory import MemorySaver

from app.agents.registry import get_agent_profile
from app.agents.resolver import resolve_agent_profile
from app.graph.graph import build_graph
from app.tools.patient_context import get_patient_data
from app.tools.registry import legacy_tools
from tests.fakes import FakeToolAwareModel


@pytest.fixture
def session():
    """Sesión de lectura con una config válida de psicología."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = SimpleNamespace(
        config={
            "system_prompt": "Psicología oficial",
            "tools": [],
            "retrieval_config": {
                "enabled": True,
                "knowledge_base_ids": ["kb-configurada"],
                "top_k": 3,
            },
        }
    )
    # La búsqueda slug→GUID del resolver no encuentra el AgentType en estos
    # tests (no se sembrica el catálogo); la clave sigue siendo el slug.
    result.first.return_value = None
    return AsyncMock(execute=AsyncMock(return_value=result))


@pytest.fixture
def scope(monkeypatch):
    resolver = AsyncMock(return_value=["kb-global", "kb-psychology"])
    monkeypatch.setattr("app.agents.resolver.resolve_knowledge_base_ids", resolver)
    return resolver


async def test_enabled_retrieval_uses_resolved_ids_and_top_k(session, scope, monkeypatch):
    factory = MagicMock(return_value=MagicMock(name="retrieve_knowledge"))
    monkeypatch.setattr("app.agents.resolver.make_retrieve_tool", factory)

    profile = await resolve_agent_profile(session, "psychology")

    scope.assert_awaited_once_with(session, "psychology", ["kb-configurada"])
    factory.assert_called_once_with(["kb-global", "kb-psychology"], 3)
    assert profile.extra_tools == (get_patient_data, factory.return_value)
    assert profile.tools == ()
    assert profile.system_prompt == "Psicología oficial"


async def test_disabled_retrieval_adds_no_tools(session, scope):
    row = session.execute.return_value.scalar_one_or_none.return_value
    row.config["retrieval_config"]["enabled"] = False

    profile = await resolve_agent_profile(session, "psychology")

    assert profile.extra_tools == (get_patient_data,)
    scope.assert_not_awaited()


async def test_enabled_without_resolved_kbs_adds_no_tools(session, scope, monkeypatch):
    scope.return_value = []
    factory = MagicMock()
    monkeypatch.setattr("app.agents.resolver.make_retrieve_tool", factory)

    profile = await resolve_agent_profile(session, "psychology")

    assert profile.extra_tools == (get_patient_data,)
    scope.assert_awaited_once()
    factory.assert_not_called()


@pytest.mark.parametrize("config", [None, {"retrieval_config": {"enabled": "inválido"}}])
async def test_missing_or_invalid_config_falls_back_to_code(session, scope, config):
    session.execute.return_value.scalar_one_or_none.return_value = (
        None if config is None else SimpleNamespace(config=config)
    )

    profile = await resolve_agent_profile(session, "psychology")

    assert profile == replace(get_agent_profile("psychology"), extra_tools=(get_patient_data,))
    assert profile.extra_tools == (get_patient_data,)
    scope.assert_not_awaited()


async def test_scope_is_refreshed_on_each_resolution(session, scope, monkeypatch):
    factory = MagicMock()
    scope.side_effect = [["kb-anterior"], ["kb-nueva"]]
    monkeypatch.setattr("app.agents.resolver.make_retrieve_tool", factory)
    await resolve_agent_profile(session, "psychology")
    await resolve_agent_profile(session, "psychology")

    assert [call.args for call in factory.call_args_list] == [
        (["kb-anterior"], 3),
        (["kb-nueva"], 3),
    ]


async def test_empty_tool_names_bind_base_tools_then_retrieval(session, scope, monkeypatch):
    profile = await resolve_agent_profile(session, "psychology")
    bound = []

    def bind_tools(self, tools, **kwargs):
        # El fake normal ignora bind_tools; validar esquemas detecta strings vacíos.
        bound.append([convert_to_openai_tool(tool)["function"]["name"] for tool in tools])
        return self

    monkeypatch.setattr(FakeToolAwareModel, "bind_tools", bind_tools)

    async def resolve_profile(state):
        return profile

    graph = build_graph(
        model=FakeToolAwareModel(responses=[AIMessage(content="Respuesta sin consulta")]),
        checkpointer=MemorySaver(),
        profile_resolver=resolve_profile,
    )
    await graph.ainvoke(
        {"input": "Tengo ansiedad"}, config={"configurable": {"thread_id": "rag-bind"}}
    )

    assert bound == [
        [
            *[tool.name for tool in legacy_tools()],
            "get_patient_data",
            "retrieve_knowledge",
        ]
    ]


@pytest.mark.parametrize("key", ["psychology", "medical", "nutrition", "base", "custom"])
@pytest.mark.parametrize("source", ["db", "code", "invalid"])
async def test_patient_context_is_only_added_to_clinical_profiles(session, scope, key, source):
    """La disponibilidad no depende de RAG, ni de disponer de user_id al resolver."""
    if key == "custom" and source != "db":
        # Un perfil desconocido sin config conserva el error de resolución anterior.
        session.execute.return_value.scalar_one_or_none.return_value = None
        with pytest.raises(KeyError):
            await resolve_agent_profile(session, key)
        return
    if source == "code":
        session.execute.return_value.scalar_one_or_none.return_value = None
    else:
        row = session.execute.return_value.scalar_one_or_none.return_value
        row.config = {"retrieval_config": {"enabled": False}} if source == "db" else {"tools": 42}

    profile = await resolve_agent_profile(session, key)

    expected = (get_patient_data,) if key in {"psychology", "medical", "nutrition"} else ()
    assert profile.extra_tools == expected
    scope.assert_not_awaited()


@pytest.mark.parametrize("key", ["base", "custom", "psychology", "medical", "nutrition", None])
async def test_default_binding_does_not_leak_patient_tool(session, scope, monkeypatch, key):
    profile = await resolve_agent_profile(session, key) if key else None
    bound = []

    def bind_tools(self, tools, **kwargs):
        bound.extend(convert_to_openai_tool(tool)["function"]["name"] for tool in tools)
        return self

    monkeypatch.setattr(FakeToolAwareModel, "bind_tools", bind_tools)

    async def resolve_profile(state):
        return profile

    graph = build_graph(
        model=FakeToolAwareModel(responses=[AIMessage(content="Respuesta")]),
        checkpointer=MemorySaver(),
        profile_resolver=resolve_profile,
    )
    await graph.ainvoke(
        {"input": "hola"}, config={"configurable": {"thread_id": "patient-binding"}}
    )
    assert bound.count("get_patient_data") == (
        1 if key in {"psychology", "medical", "nutrition"} else 0
    )


async def test_slug_key_resolves_guid_for_kb_scope(scope, monkeypatch):
    """El slug del router mapea al GUID del AgentType (filas y KBs viven por GUID)."""
    guid = "2f18a9b7-34cc-4b5a-8d63-c5ef7b7a2f97"
    guid_lookup = MagicMock()
    guid_lookup.first.return_value = (guid,)
    row_result = MagicMock()
    row_result.scalar_one_or_none.return_value = SimpleNamespace(
        config={
            "system_prompt": "Psicología oficial",
            "tools": [],
            "retrieval_config": {
                "enabled": True,
                "knowledge_base_ids": ["kb-configurada"],
                "top_k": 3,
            },
        }
    )
    session = AsyncMock(execute=AsyncMock(side_effect=[guid_lookup, row_result]))
    factory = MagicMock(return_value=MagicMock(name="retrieve_knowledge"))
    monkeypatch.setattr("app.agents.resolver.make_retrieve_tool", factory)

    profile = await resolve_agent_profile(session, "psychology")

    scope.assert_awaited_once_with(session, guid, ["kb-configurada"])
    assert profile.extra_tools == (get_patient_data, factory.return_value)
    assert profile.system_prompt == "Psicología oficial"


async def test_guid_row_wins_over_fresh_slug_seed(scope, monkeypatch):
    """El re-seed del backend (fila slug, synced_at fresco) NO tapa la fila GUID."""
    guid = "2f18a9b7-34cc-4b5a-8d63-c5ef7b7a2f97"
    guid_lookup = MagicMock()
    guid_lookup.first.return_value = (guid,)
    fila_viva = SimpleNamespace(
        config={
            "system_prompt": "Psicología oficial v4",
            "tools": [],
            "retrieval_config": {
                "enabled": True,
                "knowledge_base_ids": ["kb-configurada"],
                "top_k": 3,
            },
        }
    )
    tuple_guid_row = MagicMock()
    tuple_guid_row.scalar_one_or_none.return_value = fila_viva
    tuple_slug_row = MagicMock()
    tuple_slug_row.scalar_one_or_none.return_value = SimpleNamespace(
        config={"system_prompt": "Seed viejo", "tools": [], "retrieval_config": {"enabled": False}}
    )
    session = AsyncMock(execute=AsyncMock(side_effect=[guid_lookup, tuple_guid_row]))
    factory = MagicMock(return_value=MagicMock(name="retrieve_knowledge"))
    monkeypatch.setattr("app.agents.resolver.make_retrieve_tool", factory)

    profile = await resolve_agent_profile(session, "psychology")

    # Consulta 1: lookup del GUID; consulta 2: fila por GUID (la slug nunca se ve).
    assert session.execute.await_count == 2
    assert profile.system_prompt == "Psicología oficial v4"
    assert profile.extra_tools == (get_patient_data, factory.return_value)


async def test_slug_row_fallback_when_guid_row_missing(scope, monkeypatch):
    """Sin fila GUID activa (seed solo-slug), la fila slug sigue sirviendo."""
    guid = "2f18a9b7-34cc-4b5a-8d63-c5ef7b7a2f97"
    guid_lookup = MagicMock()
    guid_lookup.first.return_value = (guid,)
    slug_row = MagicMock()
    slug_row.scalar_one_or_none.return_value = SimpleNamespace(
        config={
            "system_prompt": "Psicología por slug",
            "tools": [],
            "retrieval_config": {"enabled": False},
        }
    )
    session = AsyncMock(
        execute=AsyncMock(
            side_effect=[
                guid_lookup,
                MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
                slug_row,
            ]
        )
    )
    factory = MagicMock()
    monkeypatch.setattr("app.agents.resolver.make_retrieve_tool", factory)

    profile = await resolve_agent_profile(session, "psychology")

    assert session.execute.await_count == 3
    scope.assert_not_awaited()
    assert profile.system_prompt == "Psicología por slug"
    assert profile.extra_tools == (get_patient_data,)
    factory.assert_not_called()


async def test_guid_key_skips_slug_lookup(session, scope, monkeypatch):
    """Una clave que ya es UUID no consulta el catálogo de slugs."""
    key = "2f18a9b7-34cc-4b5a-8d63-c5ef7b7a2f97"
    profile = await resolve_agent_profile(session, key)
    # Solo UNA llamada: la fila; la resolución de slug no se ejecutó.
    assert session.execute.await_count == 1
    assert profile.system_prompt == "Psicología oficial"
