"""Integración router→modelo efectivo→tools→checkpoint con modelos falsos."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from app.agents.prompts import PATIENT_SCOPE_POLICY
from app.agents.runtime_registry import AgentRuntimeRegistry
from app.graph.graph import build_graph
from app.vita import runtime
from app.vita.contracts import ContextEnvelope
from app.vita.routing import capability_scope
from tests.fakes import RecordingFakeModel
from tests.test_runtime_registry import AGENT_ID, VERSION_ID, make_session_with_row


@pytest.mark.parametrize(
    "text,scope",
    [
        ("Mi plan nutricional", "nutrition"),
        ("¿Qué puedo ver de mi alimentación?", "nutrition"),
        ("Show me my nutrition plan", "nutrition"),
        ("My meal plan", "nutrition"),
        ("Mi entreno de hoy", "exercise"),
        ("My training today", "exercise"),
        ("Marca mi rutina como completada", "exercise"),
        ("Complete my exercise", "exercise"),
        ("Mis tests pendientes", "assessments"),
        ("My pending assessments", "assessments"),
        ("Pon mi podcast asignado", "podcast"),
        ("Play my assigned podcast", "podcast"),
        ("¿Qué tengo para hoy?", "day"),
        ("What is my day?", "day"),
        ("Mi progreso", "progress"),
        ("Show my progress", "progress"),
    ],
)
def test_representative_personal_queries_have_selective_read_route(text, scope):
    assert capability_scope(text) == scope


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("channel", ["text", "voice"])
async def test_base_and_specialist_can_read_facts_without_erp_tool_config(
    monkeypatch, explicit, channel
):
    checkpoint = MemorySaver()
    model = RecordingFakeModel(
        responses=[
            AIMessage(
                content="", tool_calls=[{"name": "get_my_nutrition", "args": {}, "id": "read"}]
            ),
            AIMessage(content="El menú vigente figura en tu plan"),
        ]
    )
    if channel == "voice":
        model.responses = [AIMessage(content="El menú vigente figura en tu plan")]
    model_options = []

    def factory(**options):
        model_options.append(options)
        return model

    registry = AgentRuntimeRegistry(model_factory=factory, checkpointer_factory=lambda: checkpoint)
    monkeypatch.setattr(runtime, "runtime_registry", registry)
    monkeypatch.setattr(runtime, "get_chat_model", factory)
    monkeypatch.setattr(
        runtime,
        "read_catalog",
        AsyncMock(
            return_value=[
                {
                    "id": AGENT_ID,
                    "runtimeKey": AGENT_ID,
                    "slug": "new-specialist",
                    "name": "Nuevo",
                    "versionId": VERSION_ID,
                    "versionNumber": 1,
                    "routing": {"keywords": [], "topics": [], "channels": ["text", "voice"]},
                }
            ]
        ),
    )
    facts = AsyncMock(return_value=ContextEnvelope(status="ok", data={"menu": "vigente"}))
    monkeypatch.setattr("app.tools.vita.read_context", facts)

    @asynccontextmanager
    async def unlocked(_):
        yield

    monkeypatch.setattr("app.vita.locking.thread_guard", unlocked)
    base = build_graph(model=model, checkpointer=checkpoint)
    config = {"configurable": {"thread_id": "owner::thread", "user_id": "owner"}}
    turn = await runtime.resolve_turn(
        user_id="owner",
        text="Mi plan nutricional",
        session=make_session_with_row(
            {
                "provider": "openai",
                "model": "specialist-effective",
                "tools": [],
            }
        ),
        base_graph=base,
        config=config,
        explicit=AGENT_ID if explicit else None,
        channel=channel,
    )
    result = await turn.graph.ainvoke(
        {"input": "Mi plan nutricional", "agent": turn.agent_id}, config=config
    )
    facts.assert_awaited_once_with("nutrition", "owner")
    assert result["tools_used"] == ["get_my_nutrition"]
    if explicit:
        assert turn.agent_id == AGENT_ID and turn.version_id == VERSION_ID
        assert all(o["model"] == "specialist-effective" for o in model_options)
        assert all(o["provider"] == "openai" for o in model_options)
    assert len(model.prompts) == (1 if channel == "voice" else 2)
    assert all(p[0].content == PATIENT_SCOPE_POLICY for p in model.prompts)


async def test_classifier_provider_failure_falls_back(monkeypatch):
    def broken(**_):
        raise RuntimeError("provider failed")

    monkeypatch.setattr(runtime, "get_chat_model", broken)
    from tests.test_vita_routing import agent

    assert await runtime.classify("Consulta ambigua", [agent("new", "new", [])]) is None


@pytest.mark.parametrize("recorded", [False, True])
async def test_task_completion_is_read_only_and_backend_status_is_the_only_evidence(
    monkeypatch, recorded
):
    text = (
        "Consta completada en el programa"
        if recorded
        else "Está pendiente; abre la rutina para confirmar"
    )
    model = RecordingFakeModel(
        responses=[
            AIMessage(
                content="", tool_calls=[{"name": "get_my_exercise", "args": {}, "id": "task"}]
            ),
            AIMessage(content=text),
        ]
    )
    facts = AsyncMock(
        return_value=ContextEnvelope(
            status="ok",
            data={"taskStatus": "completed" if recorded else "pending"},
        )
    )
    monkeypatch.setattr("app.tools.vita.read_context", facts)
    from app.tools.registry import TOOLS_BY_NAME

    graph = build_graph(
        model=model, tools=[TOOLS_BY_NAME["get_my_exercise"]], checkpointer=MemorySaver()
    )
    result = await graph.ainvoke(
        {"input": "Marca mi rutina como completada"},
        config={
            "configurable": {"thread_id": f"read-{recorded}", "user_id": "owner"},
        },
    )
    assert result["tools_used"] == ["get_my_exercise"]
    assert result["messages"][-1].content == text
    assert (
        "Never claim a task was completed or XP awarded without a canonical backend result"
        in model.prompts[0][0].content
    )
    assert "Reading, opening or listening never completes" in model.prompts[1][0].content
