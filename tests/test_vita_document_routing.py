"""Regresiones ES/EN: documentación no se confunde con ficha personal."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from app.agents.runtime_registry import AgentRuntimeRegistry
from app.graph.graph import build_graph
from app.vita import runtime
from app.vita.routing import capability_scope
from tests.fakes import RecordingFakeModel
from tests.test_runtime_registry import AGENT_ID, VERSION_ID, make_session_with_row


@pytest.mark.parametrize(
    "query",
    [
        "Según los documentos de psicología, explica los ejercicios de respiración",
        "According to the psychology protocol, explain the breathing exercise",
        "¿Qué dicen las guías de psicología sobre los tests de ansiedad?",
        "What do the psychology guidelines say about anxiety tests?",
        "Explica el ejercicio como concepto de psicología",
    ],
)
def test_documentary_or_conceptual_questions_do_not_read_personal_plans(query):
    assert capability_scope(query) is None


@pytest.mark.parametrize("channel", ["text", "voice"])
@pytest.mark.parametrize("specialist", [True, False])
async def test_retrieval_is_prepared_once_from_authorized_scope(monkeypatch, channel, specialist):
    checkpoint = MemorySaver()
    model = RecordingFakeModel(responses=[AIMessage(content="Según la guía, practica una pausa.")])
    retrieved = []

    def scoped_tool(knowledge_base_ids, top_k=5):
        @tool
        async def retrieve_knowledge(query: str) -> str:
            """Consulta exclusivamente las fuentes autorizadas."""
            retrieved.append((knowledge_base_ids, query))
            return "[Fuente: Guía de bienestar — Sección: Pausa]\nHaz una pausa breve."

        return retrieve_knowledge

    monkeypatch.setattr(runtime, "make_retrieve_tool", scoped_tool)
    monkeypatch.setattr("app.agents.runtime_registry.make_retrieve_tool", scoped_tool)
    registry = AgentRuntimeRegistry(
        model_factory=lambda **_: model,
        checkpointer_factory=lambda: checkpoint,
    )
    monkeypatch.setattr(runtime, "runtime_registry", registry)
    monkeypatch.setattr(runtime, "get_chat_model", lambda **_: model)
    monkeypatch.setattr(
        runtime,
        "read_catalog",
        AsyncMock(
            return_value=[
                {
                    "id": AGENT_ID,
                    "runtimeKey": AGENT_ID,
                    "name": "Psicología",
                    "slug": "psychology",
                    "versionId": VERSION_ID,
                    "versionNumber": 1,
                    "routing": {
                        "keywords": ["psicología", "psychology"],
                        "channels": ["text", "voice"],
                    },
                }
            ]
            if specialist
            else []
        ),
    )
    monkeypatch.setattr(runtime, "classify", AsyncMock(return_value=None))

    @asynccontextmanager
    async def unlocked(_):
        yield

    monkeypatch.setattr("app.vita.locking.thread_guard", unlocked)
    session = make_session_with_row(
        {
            "retrieval_config": {"enabled": True},
            "tools": [],
        },
        knowledge_bases=[{"id": "global", "scope": "Global"}]
        + ([{"id": "own-psychology", "scope": "Agent"}] if specialist else []),
    )
    query = "Según la documentación de psicología, explica los ejercicios de respiración"
    config = {
        "configurable": {
            "thread_id": "owner::documents",
            "user_id": "owner",
            "message_channel": channel,
        }
    }
    turn = await runtime.resolve_turn(
        user_id="owner",
        text=query,
        session=session,
        base_graph=build_graph(model=model, checkpointer=checkpoint),
        config=config,
        channel=channel,
    )
    result = await turn.graph.ainvoke({"input": query, "agent": turn.agent_id}, config=config)
    assert turn.agent_id == (AGENT_ID if specialist else "base")
    assert result["tools_used"] == ["retrieve_knowledge"]
    assert len(model.prompts) == 1
    assert retrieved == [(["global", "own-psychology"] if specialist else ["global"], query)]
    assert "Guía de bienestar" in str(model.prompts[0])


async def test_greeting_does_not_query_global_knowledge(monkeypatch):
    checkpoint = MemorySaver()
    model = RecordingFakeModel(responses=[AIMessage(content="Hola")])
    monkeypatch.setattr(runtime, "read_catalog", AsyncMock(return_value=[]))
    monkeypatch.setattr(runtime, "get_chat_model", lambda **_: model)
    scope = AsyncMock()
    monkeypatch.setattr(runtime, "resolve_knowledge_base_ids", scope)
    await runtime.resolve_turn(
        user_id="owner",
        text="Hola Vita cómo estás hoy",
        session=None,
        base_graph=build_graph(model=model, checkpointer=checkpoint),
        config={"configurable": {"thread_id": "owner::greeting"}},
        channel="voice",
    )
    scope.assert_not_awaited()
