"""Regresiones de capacidades, límites y cancelación sin proveedores reales."""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from app.agents.runtime_registry import AgentRuntimeRegistry
from app.graph.graph import build_graph
from app.tools.registry import TOOLS_BY_NAME
from tests.fakes import FakeToolAwareModel, RecordingFakeModel


def call(name, args=None, id="call"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args or {}, "id": id}])


async def test_empty_explicit_tools_reject_model_call_at_execution():
    graph = build_graph(
        model=FakeToolAwareModel(responses=[call("get_current_time"), AIMessage(content="Fin")]),
        tools=[],
        checkpointer=MemorySaver(),
    )
    result = await graph.ainvoke(
        {"input": "Hola"},
        config={"configurable": {"thread_id": "empty"}},
    )
    assert result["tools_used"] == []
    assert json.loads(result["messages"][-2].content)["code"] == "tool_not_authorized"


def test_legacy_config_does_not_inherit_new_tools(monkeypatch):
    @tool
    def future_write() -> str:
        """Escritura futura que nunca debe heredarse."""
        raise AssertionError("No ejecutar")

    monkeypatch.setitem(TOOLS_BY_NAME, "future_write", future_write)
    selected = AgentRuntimeRegistry()._select_tools([], "old")
    assert "future_write" not in {t.name for t in selected}
    assert "suggest_appointment" not in {t.name for t in selected}


async def test_appointment_explicitly_selected_is_still_denied():
    graph = build_graph(
        model=RecordingFakeModel(
            responses=[
                call("suggest_appointment", {"reason": "solicitud"}),
                AIMessage(content="No disponible"),
            ]
        ),
        tools=[TOOLS_BY_NAME["suggest_appointment"]],
        checkpointer=MemorySaver(),
    )
    result = await graph.ainvoke(
        {"input": "Quiero una cita"},
        config={"configurable": {"thread_id": "no-appointments"}},
    )
    assert result["suggestions"] == []
    assert result["tools_used"] == []


async def test_budget_stops_even_model_that_ignores_disabled_tools():
    graph = build_graph(
        model=FakeToolAwareModel(
            responses=[
                call("get_current_time", id="first"),
                call("get_current_time", id="second"),
            ]
        ),
        checkpointer=MemorySaver(),
        max_tool_calls=1,
    )
    result = await graph.ainvoke(
        {"input": "Hora"},
        config={"configurable": {"thread_id": "budget"}},
    )
    assert result["tool_call_count"] == 1
    assert result["tools_used"] == ["get_current_time"]
    assert not result["messages"][-1].tool_calls


async def test_timeout_cancels_tool_and_next_turn_recovers():
    cancelled = asyncio.Event()

    @tool
    async def slow() -> str:
        """Lectura lenta."""
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    model = FakeToolAwareModel(
        responses=[
            call("slow"),
            AIMessage(content="Intenta luego"),
            AIMessage(content="Respuesta normal"),
        ]
    )
    graph = build_graph(
        model=model, tools=[slow], checkpointer=MemorySaver(), tool_timeout_seconds=0.02
    )
    config = {"configurable": {"thread_id": "timeout"}}
    result = await graph.ainvoke({"input": "Consulta"}, config=config)
    assert cancelled.is_set()
    assert "tool_timeout" in result["messages"][-2].content
    result = await graph.ainvoke({"input": "Consulta otra vez"}, config=config)
    assert result["tool_call_count"] == 0
    assert result["messages"][-1].content == "Respuesta normal"


async def test_external_cancellation_propagates_to_tool():
    started, cancelled = asyncio.Event(), asyncio.Event()

    @tool
    async def slow() -> str:
        """Lectura cancelable."""
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    graph = build_graph(
        model=FakeToolAwareModel(responses=[call("slow")]),
        tools=[slow],
        checkpointer=MemorySaver(),
    )
    task = asyncio.create_task(
        graph.ainvoke(
            {"input": "Consulta"},
            config={"configurable": {"thread_id": "cancel"}},
        )
    )
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
