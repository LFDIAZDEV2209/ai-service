"""Regresiones puras: calendario, identidad y memoria sin alcance de hilo."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from app.graph import nodes
from app.graph.graph import build_graph
from app.history import service
from app.history.service import retention_worker as real_retention_worker
from app.memory import checkpointer
from app.voice.store import VoiceContextError
from tests.fakes import RecordingFakeModel


@pytest.mark.parametrize(
    ("instant", "month"),
    [
        ("2026-12-01T04:59:59+00:00", "2026-11-01"),
        ("2026-12-01T05:00:00+00:00", "2026-12-01"),
        ("2027-01-01T05:00:00+00:00", "2027-01-01"),
        ("2028-03-01T04:59:59+00:00", "2028-02-01"),
    ],
)
def test_bogota_calendar_month_including_year_and_leap_year(instant, month):
    assert service.calendar_month(datetime.fromisoformat(instant)).isoformat() == month


def test_reject_naive_clock_and_ambiguous_storage_identity():
    with pytest.raises(ValueError, match="timezone"):
        service.calendar_month(datetime(2026, 1, 1))
    for user, thread in (("a::b", "c"), ("a", "b::c"), ("", "b")):
        with pytest.raises(VoiceContextError):
            service.storage_key(user, thread)
    assert service.calendar_month(datetime(2026, 12, 1, 5, tzinfo=UTC)).day == 1


@pytest.mark.parametrize("memory_enabled", [False, True])
async def test_vita_cannot_rehydrate_unscoped_summary_or_write_shared_memory(
    monkeypatch, memory_enabled
):
    factory = Mock(side_effect=AssertionError("La memoria sin hilo no pertenece a Vita"))
    monkeypatch.setattr(nodes, "_default_service", factory)
    monkeypatch.setattr(nodes, "_default_adaptive_service", factory)
    model = RecordingFakeModel(responses=[AIMessage(content="fresh response")])
    graph = build_graph(model=model, checkpointer=MemorySaver(), enable_memory=memory_enabled)
    config = {
        "configurable": {
            "thread_id": "owner::thread",
            "user_id": "owner",
            "vita_history": {"revision": 1},
        }
    }
    result = await graph.ainvoke(
        {
            "input": "fresh fixture",
            "agent": "base",
            "memory_context": "OLD_CONVERSATION",
            "experience_context": "OLD_EXPERIENCE",
        },
        config=config,
    )
    assert result["memory_context"] == "" and result["experience_context"] == ""
    factory.assert_not_called()
    assert "OLD_CONVERSATION" not in str(model.prompts)
    assert "OLD_EXPERIENCE" not in str(model.prompts)


async def test_worker_sweeps_on_start_and_cancels_on_shutdown(monkeypatch):
    called = asyncio.Event()
    saver = object()

    async def sweep(actual, **_):
        assert actual is saver
        called.set()

    monkeypatch.setattr(service.history_service, "sweep", sweep)
    monkeypatch.setattr(checkpointer, "get_checkpointer", AsyncMock(return_value=saver))
    async with real_retention_worker():
        await asyncio.wait_for(called.wait(), 2)
        task = next(t for t in asyncio.all_tasks() if t.get_name() == "vita-history-retention")
    assert task.done()


async def test_voice_binding_without_explicit_opt_in_cannot_register():
    from app.voice.schemas import VoiceSessionRequest
    from app.voice.vita_session import reserve_binding

    with pytest.raises(VoiceContextError, match="voice_history_opt_in_required"):
        await reserve_binding(
            VoiceSessionRequest(user_id="owner", thread_id="thread"),
            {"conversation_id": "fixture"},
            agent_id="fixture",
            version_id=None,
            runtime_mode="legacy",
        )
