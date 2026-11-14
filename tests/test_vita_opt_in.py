"""El flag global no convierte playground ERP ni clientes antiguos en pacientes Vita."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from app.agents.runtime_config import AgentRuntimeConfig
from app.agents.runtime_registry import AgentRuntimeRegistry
from app.api.deps import get_db_session, get_graph
from app.api.main import create_app
from app.api.routes import chat
from app.api.schemas import ChatRequest
from app.core.config import get_settings
from app.graph.graph import build_graph
from app.vita import runtime
from tests.fakes import RecordingFakeModel
from tests.test_runtime_registry import AGENT_ID, VERSION_ID, make_session_with_row


@pytest.mark.parametrize("supports", [None, False])
@pytest.mark.parametrize("explicit", [False, True])
async def test_enabled_old_or_erp_request_preserves_legacy_runtime(monkeypatch, supports, explicit):
    monkeypatch.setattr(get_settings(), "vita_runtime_enabled", True)
    catalog = AsyncMock(side_effect=AssertionError("No PatientProfile/catalog for ERP admin"))
    monkeypatch.setattr(runtime, "read_catalog", catalog)
    graph = object()
    cfg = AgentRuntimeConfig(provider="openai", model="erp-configured-model")
    registry = AsyncMock(
        return_value=SimpleNamespace(
            graph=graph,
            runtime_config=cfg,
            version_id=VERSION_ID,
        )
    )
    monkeypatch.setattr(chat.runtime_registry, "get_agent", registry)
    payload = {"message": "Consulta ERP", "user_id": "admin-without-patient-profile"}
    if explicit:
        payload["agent_type_id"] = AGENT_ID
    if supports is not None:
        payload["supports_vita"] = supports
    request = ChatRequest(**payload)
    result = await chat._resolve_graph(request, object(), object(), "thread")
    assert not chat._is_vita_request(request)
    if explicit:
        assert result[0] is graph and result[2:] == (VERSION_ID, "openai", "erp-configured-model")
        registry.assert_awaited_once()
    else:
        assert result[0] is None  # Conserva el grafo/perfil base previo.
        registry.assert_not_awaited()
    catalog.assert_not_awaited()


async def test_whatsapp_stays_legacy_even_if_client_sets_capability(monkeypatch):
    monkeypatch.setattr(get_settings(), "vita_runtime_enabled", True)
    request = ChatRequest(message="Hola", agent="whatsapp", supports_vita=True)
    assert not chat._is_vita_request(request)
    assert (await chat._resolve_graph(request, object()))[0] is None


@pytest.mark.parametrize("authorized", [False, True])
async def test_new_opted_in_client_uses_authenticated_catalog_without_extra_permissions(
    monkeypatch, authorized
):
    settings = get_settings()
    monkeypatch.setattr(settings, "vita_runtime_enabled", True)
    monkeypatch.setattr(settings, "vita_transcript_sync_enabled", False)
    model = RecordingFakeModel(responses=[AIMessage(content="Respuesta autorizada")])
    checkpoint = MemorySaver()
    base = build_graph(model=model, checkpointer=checkpoint)
    catalog = AsyncMock(
        return_value=[
            {
                "id": AGENT_ID,
                "runtimeKey": AGENT_ID,
                "slug": "new-specialist",
                "name": "Especialista",
                "versionId": VERSION_ID,
                "versionNumber": 1,
                "routing": {"keywords": [], "topics": [], "channels": ["text", "voice"]},
            }
        ]
        if authorized
        else []
    )
    monkeypatch.setattr(runtime, "read_catalog", catalog)
    registry = AgentRuntimeRegistry(
        model_factory=lambda **_: model, checkpointer_factory=lambda: checkpoint
    )
    monkeypatch.setattr(runtime, "runtime_registry", registry)

    @asynccontextmanager
    async def unlocked(_):
        yield

    monkeypatch.setattr("app.vita.locking.thread_guard", unlocked)
    db = make_session_with_row({"provider": "openai", "model": "erp-model"})
    db.add = MagicMock()
    db.get.return_value = None
    app = create_app()
    app.dependency_overrides[get_graph] = lambda: base
    app.dependency_overrides[get_db_session] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://ai.test"
    ) as client:
        response = await client.post(
            "/api/v1/chat",
            json={
                "message": "Hola",
                "user_id": "patient-owner",
                "agent_type_id": AGENT_ID,
                "supports_vita": True,
            },
            headers={"X-Internal-Key": settings.internal_api_key},
        )
    catalog.assert_awaited_once_with("patient-owner")
    if authorized:
        assert response.status_code == 200
        assert response.json()["agent"] == AGENT_ID and response.json()["version_id"] == VERSION_ID
        assert len(model.prompts) == 1
    else:
        assert response.status_code >= 400 and not model.prompts


def test_global_flag_off_always_preserves_legacy(monkeypatch):
    monkeypatch.setattr(get_settings(), "vita_runtime_enabled", False)
    assert not chat._is_vita_request(ChatRequest(message="Hola", supports_vita=True))
