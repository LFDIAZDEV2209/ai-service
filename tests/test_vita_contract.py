"""Contrato HTTP BACK v1 ejecutado con transporte simulado, sin pacientes reales."""

import asyncio

import httpx
import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from app.core.config import get_settings
from app.graph.graph import build_graph
from app.tools.registry import TOOLS_BY_NAME
from app.vita.client import read_context
from tests.fakes import FakeToolAwareModel

REAL_CLIENT = httpx.AsyncClient


@pytest.fixture
def backend(monkeypatch):
    requests = []
    settings = get_settings()
    monkeypatch.setattr(settings, "backend_base_url", "https://backend.test")

    def install(body, status=200):
        async def handle(request):
            requests.append(request)
            assert request.headers["x-internal-key"] == settings.internal_api_key
            return httpx.Response(status, json=body)

        monkeypatch.setattr(
            "app.vita.client.httpx.AsyncClient",
            lambda **kwargs: REAL_CLIENT(
                **kwargs,
                transport=httpx.MockTransport(handle),
            ),
        )

    return install, requests


def envelope(**overrides):
    return {
        "status": "ok",
        "asOf": "2026-11-14T14:00:00Z",
        "localDate": "2026-11-14",
        "resourceVersion": "rev-a",
        "data": {"rest": True},
        "actions": [
            {
                "type": "navigate",
                "destination": "exercise",
                "resourceId": None,
                "localDate": "2026-11-14",
                "resourceVersion": "rev-a",
            }
        ],
        "sources": [{"capability": "exercise", "caseUse": "GetSnapshotQuery"}],
        **overrides,
    }


@pytest.mark.parametrize(
    "scope", ["day", "nutrition", "assessments", "podcast", "exercise", "progress"]
)
async def test_six_tools_use_only_authenticated_identity_and_export_actions(scope, backend):
    install, requests = backend
    install(envelope())
    name = f"get_my_{scope}"
    graph = build_graph(
        model=FakeToolAwareModel(
            responses=[
                AIMessage(content="", tool_calls=[{"name": name, "args": {}, "id": "read"}]),
                AIMessage(content="Hoy corresponde descanso"),
            ]
        ),
        tools=[TOOLS_BY_NAME[name]],
        checkpointer=MemorySaver(),
    )
    result = await graph.ainvoke(
        {"input": "Lo de hoy"},
        config={"configurable": {"thread_id": f"a::{scope}", "user_id": "a"}},
    )
    assert len(requests) == 1
    assert requests[0].url.path == "/api/v1/internal/vita/context"
    assert dict(requests[0].url.params) == {"userId": "a", "scope": scope}
    assert result["app_actions"] == envelope()["actions"]
    assert result["data_sources"] == envelope()["sources"]
    assert result["tools_used"] == [name]


@pytest.mark.parametrize("args", [{"user_id": "other"}, {"userId": "other"}, {"date": "tomorrow"}])
async def test_model_cannot_choose_identity_or_date(args, backend):
    install, requests = backend
    install(envelope())
    graph = build_graph(
        model=FakeToolAwareModel(
            responses=[
                AIMessage(
                    content="", tool_calls=[{"name": "get_my_day", "args": args, "id": "foreign"}]
                ),
                AIMessage(content="No autorizado"),
            ]
        ),
        tools=[TOOLS_BY_NAME["get_my_day"]],
        checkpointer=MemorySaver(),
    )
    await graph.ainvoke(
        {"input": "Otro paciente"},
        config={
            "configurable": {"thread_id": "a::only", "user_id": "a"},
        },
    )
    assert requests == []


@pytest.mark.parametrize("status", ["empty", "unavailable", "forbidden", "stale"])
async def test_backend_states_are_preserved_and_cannot_offer_actions(status, backend):
    install, _ = backend
    install(
        envelope(status=status),
        {"empty": 200, "unavailable": 503, "forbidden": 403, "stale": 409}[status],
    )
    result = await read_context("nutrition", "a")
    assert result.status == status
    assert result.actions == []


@pytest.mark.parametrize(
    "code,expected", [(403, "forbidden"), (404, "unavailable"), (500, "unavailable")]
)
async def test_http_failures_are_not_misrepresented_as_no_plan(code, expected, backend):
    install, _ = backend
    install(envelope(status="forbidden") if code == 403 else {}, code)
    assert (await read_context("nutrition", "a")).status == expected


@pytest.mark.parametrize("code,status", [(200, "stale"), (409, "ok"), (503, "ok"), (403, "ok")])
async def test_incoherent_http_envelope_cannot_supply_facts_or_actions(code, status, backend):
    install, _ = backend
    install(envelope(status=status), code)
    result = await read_context("day", "a")
    assert result.status == "unavailable"
    assert result.data is None and result.actions == [] and result.sources == []


async def test_valid_stale_409_is_preserved_without_previous_data(backend):
    install, _ = backend
    install(envelope(status="stale", data={"prior": "must not be used"}), 409)
    result = await read_context("exercise", "a")
    assert result.status == "stale" and result.resource_version == "rev-a"
    assert result.data is None and result.actions == [] and result.sources == []


@pytest.mark.parametrize("code", [403, 409, 503])
async def test_arbitrary_error_body_is_not_a_capability_envelope(code, backend):
    install, _ = backend
    install({"status": code, "title": "ProblemDetails", "data": {"secret": "not facts"}}, code)
    result = await read_context("day", "a")
    assert result.status == "unavailable"
    assert result.data is None and result.actions == []


async def test_empty_200_preserves_pending_review_without_draft_or_action(backend):
    install, _ = backend
    install(envelope(status="empty", data={"availability": "pending_review"}), 200)
    result = await read_context("nutrition", "a")
    assert result.status == "empty"
    assert result.data == {"availability": "pending_review"} and result.actions == []


async def test_arbitrary_navigation_url_is_rejected(backend):
    install, _ = backend
    install(envelope(actions=[{"type": "navigate", "destination": "https://evil.test"}]))
    assert (await read_context("day", "a")).status == "unavailable"


async def test_missing_identity_never_calls_backend(backend):
    install, requests = backend
    install(envelope())
    result = await read_context("day", "")
    assert result.status == "unavailable"
    assert not requests


async def test_http_timeout_and_cancellation_release_upstream(monkeypatch):
    started, cancelled = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(get_settings(), "backend_base_url", "https://backend.test")
    monkeypatch.setattr(get_settings(), "vita_http_timeout", 0.02)

    async def slow(request):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(
        "app.vita.client.httpx.AsyncClient",
        lambda **kwargs: REAL_CLIENT(
            **kwargs,
            transport=httpx.MockTransport(slow),
        ),
    )
    assert (await read_context("day", "a")).status == "unavailable"
    assert started.is_set() and cancelled.is_set()


async def test_response_size_is_bounded(backend):
    install, _ = backend
    install(envelope(data={"oversized": "x" * 65000}))
    assert (await read_context("day", "a")).status == "unavailable"
