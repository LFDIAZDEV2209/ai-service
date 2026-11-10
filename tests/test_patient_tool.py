"""Contexto del paciente: identidad por llamada, contrato HTTP y degradación segura."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from app.graph.graph import build_graph
from app.tools.patient_context import NO_PATIENT_DATA, get_patient_data
from app.tools.registry import TOOLS_BY_NAME
from tests.fakes import FakeToolAwareModel


@pytest.fixture
def backend(monkeypatch):
    settings = SimpleNamespace(
        backend_base_url="https://backend.test/", internal_api_key="test-key"
    )
    monkeypatch.setattr("app.tools.patient_context.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.tools.patient_context.get_config",
        lambda: {"configurable": {"user_id": "patient-1"}},
    )
    client = AsyncMock()
    client.get.return_value = httpx.Response(200, json={"patient": {"name": "María"}})
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=client)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr("app.tools.patient_context.httpx.AsyncClient", factory)
    return settings, client, factory


async def test_patient_data_serializes_json_and_uses_internal_contract(backend):
    _, client, factory = backend

    result = await get_patient_data.ainvoke({})

    assert json.loads(result) == {"patient": {"name": "María"}}
    assert "María" in result
    factory.assert_called_once_with(timeout=8.0)
    client.get.assert_awaited_once_with(
        "https://backend.test/api/v1/internal/agents/patient-context",
        params={"userId": "patient-1"},
        headers={"X-Internal-Key": "test-key"},
    )
    assert TOOLS_BY_NAME["get_patient_data"] is get_patient_data
    assert not get_patient_data.args_schema.model_fields


@pytest.mark.parametrize("status", [204, 400, 401, 404, 500, 503])
async def test_http_errors_return_neutral_message(backend, status):
    _, client, _ = backend
    client.get.return_value = httpx.Response(status)
    assert await get_patient_data.ainvoke({}) == NO_PATIENT_DATA


@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout("timeout"), RuntimeError("error"), TimeoutError()]
)
async def test_request_errors_never_escape(backend, error):
    _, client, _ = backend
    client.get.side_effect = error
    assert await get_patient_data.ainvoke({}) == NO_PATIENT_DATA


@pytest.mark.parametrize("body", [b"invalid-json", b"", b"null"])
async def test_invalid_or_empty_response_is_neutral(backend, body):
    _, client, _ = backend
    client.get.return_value = httpx.Response(200, content=body)
    assert await get_patient_data.ainvoke({}) == NO_PATIENT_DATA


@pytest.mark.parametrize("config", [{}, {"configurable": {}}, {"configurable": {"user_id": ""}}])
async def test_missing_user_does_not_call_backend(backend, monkeypatch, config):
    _, _, factory = backend
    monkeypatch.setattr("app.tools.patient_context.get_config", lambda: config)
    assert await get_patient_data.ainvoke({}) == NO_PATIENT_DATA
    factory.assert_not_called()


@pytest.mark.parametrize("field", ["backend_base_url", "internal_api_key"])
async def test_missing_settings_skip_http(backend, field):
    settings, _, factory = backend
    setattr(settings, field, "")
    assert await get_patient_data.ainvoke({}) == NO_PATIENT_DATA
    factory.assert_not_called()


async def test_missing_graph_context_never_raises(backend, monkeypatch):
    def no_context():
        raise RuntimeError("Fuera del grafo")

    monkeypatch.setattr("app.tools.patient_context.get_config", no_context)
    assert await get_patient_data.ainvoke({}) == NO_PATIENT_DATA


async def test_real_graph_reads_current_user_on_each_invocation(backend, monkeypatch):
    """La tool enlazada no retiene la identidad de una invocación anterior."""
    from langgraph.config import get_config

    monkeypatch.setattr("app.tools.patient_context.get_config", get_config)
    _, client, _ = backend
    fake = FakeToolAwareModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "get_patient_data", "args": {}, "id": "patient", "type": "tool_call"}
                ],
            ),
            AIMessage(content="Respuesta"),
        ]
    )
    graph = build_graph(model=fake, checkpointer=MemorySaver(), tools=[get_patient_data])
    for index, user_id in enumerate(["patient-1", "patient-2", None]):
        result = await graph.ainvoke(
            {"input": "Mis medidas"},
            config={"configurable": {"thread_id": f"patient-{index}", "user_id": user_id}},
        )
        tool_output = next(
            msg.content for msg in result["messages"] if isinstance(msg, ToolMessage)
        )
        assert tool_output == NO_PATIENT_DATA if user_id is None else "María" in tool_output
    assert [call.kwargs["params"] for call in client.get.await_args_list] == [
        {"userId": "patient-1"},
        {"userId": "patient-2"},
    ]
