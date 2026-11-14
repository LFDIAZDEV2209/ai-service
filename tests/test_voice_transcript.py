"""HTTP ASGI: transcript oficial ligado, sin importar bots del cliente ni generar."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from app.api.deps import get_graph
from app.api.main import create_app
from app.core.config import get_settings
from app.graph.graph import build_graph
from app.vita.client import VitaAccessError
from app.voice import transcript
from app.voice.store import VoiceContextError
from tests.fakes import RecordingFakeModel

REAL_CLIENT = httpx.AsyncClient
SID = "11111111-1111-1111-1111-111111111111"
CONFIG = {"configurable": {"thread_id": "owner::thread"}}


class BoundStore:
    """Doble de frontera; concurrencia real se verifica en test_vita_postgres."""

    def __init__(self):
        self.record = SimpleNamespace(
            id=SID,
            user_id="owner",
            patient_id="patient",
            thread_id="thread",
            agent_id="agent_reserved",
            conversation_id="conv_reserved",
            version_id=None,
            runtime_mode="vita",
            revoked=True,
            transcript_digest=None,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        self.mark_transcript_synced = AsyncMock()
        self.created = []

    async def closed_for_thread(self, user_id, thread_id):
        record = self.record
        return (
            [record]
            if (
                user_id == record.user_id
                and thread_id == record.thread_id
                and (record.revoked or record.expires_at <= datetime.now(UTC))
            )
            else []
        )

    async def get_owned(self, sid, user_id, thread_id):
        if (sid, user_id, thread_id) != (self.record.id, "owner", "thread"):
            raise VoiceContextError("voice_session_not_found", 404)
        return self.record

    async def create(self, record):
        self.created.append(record)


def provider_payload():
    # El source_event_id se comparte entre roles y no tiene por qué ser contiguo.
    return {
        "agent_id": "agent_reserved",
        "conversation_id": "conv_reserved",
        "status": "done",
        "user_id": "ignored-provider-identity",
        "reasoning": "GLOBAL_REASONING_SECRET",
        "transcript": [
            {"id": None, "role": "agent", "source_event_id": 1, "message": "Hola, soy Vita"},
            {"id": None, "role": "user", "source_event_id": 2, "message": "La misma frase"},
            {
                "id": None,
                "role": "agent",
                "source_event_id": 2,
                "message": "Respuesta",
                "reasoning": "REASONING_SECRET",
                "tool_results": [{"value": "TOOL_SECRET"}],
            },
            {"id": None, "role": "tool", "message": "TOOL_SECRET"},
            {"id": None, "role": "user", "source_event_id": 14, "message": "La misma frase"},
            {"id": None, "role": "agent", "source_event_id": 14, "message": "Respuesta"},
            {"role": "agent", "message": None, "reasoning": "REASONING_SECRET"},
            {"role": "user", "message": "BACKCHANNEL", "ignored_as_backchannel": True},
        ],
    }


@pytest.fixture
def rig(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "vita_transcript_sync_enabled", True)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "synthetic-provider-key")
    monkeypatch.setattr(settings, "elevenlabs_base_url", "https://provider.test")
    model = RecordingFakeModel(responses=[AIMessage(content="MUST_NOT_GENERATE")])
    graph = build_graph(model=model, checkpointer=MemorySaver())
    app = create_app()
    app.dependency_overrides[get_graph] = lambda: graph
    store = BoundStore()
    monkeypatch.setattr(transcript, "voice_store", store)
    consent = AsyncMock(return_value=[])
    monkeypatch.setattr(transcript, "read_catalog", consent)

    @asynccontextmanager
    async def unlocked(_):
        yield

    monkeypatch.setattr(transcript, "thread_guard", unlocked)
    state = SimpleNamespace(payload=provider_payload(), code=200, requests=[])

    async def provider(request):
        state.requests.append(request)
        assert request.url.path == "/v1/convai/conversations/conv_reserved"
        assert request.headers["xi-api-key"] == "synthetic-provider-key"
        return httpx.Response(state.code, json=state.payload)

    monkeypatch.setattr(
        transcript.httpx,
        "AsyncClient",
        lambda **kwargs: REAL_CLIENT(
            **kwargs,
            transport=httpx.MockTransport(provider),
        ),
    )
    return SimpleNamespace(
        app=app,
        graph=graph,
        model=model,
        store=store,
        provider=state,
        consent=consent,
        monkeypatch=monkeypatch,
    )


async def read(rig, *, owner="owner", sync=True, headers=None, body=None):
    async with REAL_CLIENT(
        transport=httpx.ASGITransport(app=rig.app), base_url="http://ai.test"
    ) as client:
        return await client.request(
            "GET",
            "/api/v1/threads/thread/state",
            params={
                "user_id": owner,
                "sync_voice": str(sync).lower(),
            },
            headers={"X-Internal-Key": get_settings().internal_api_key}
            if headers is None
            else headers,
            json=body,
        )


async def seed(rig):
    await rig.graph.aupdate_state(
        CONFIG,
        {
            "messages": [
                HumanMessage(id="text-before", content="Texto antes"),
                AIMessage(id="bot-before", content="Respuesta escrita antes"),
                HumanMessage(id=f"voice:{SID}:1:user", content="ASR provisional"),
                AIMessage(
                    id="internal-plan",
                    content="",
                    tool_calls=[
                        {
                            "id": "call",
                            "name": "get_current_time",
                            "args": {},
                        }
                    ],
                ),
                ToolMessage(id="internal-tool", content="RUNTIME_TOOL_SECRET", tool_call_id="call"),
                AIMessage(id=f"voice:{SID}:1:bot", content="Generación que quizá no se oyó"),
                HumanMessage(id="text-after", content="Texto después"),
                AIMessage(id="bot-after", content="Respuesta escrita después"),
            ]
        },
    )
    rig.graph.aupdate_state = AsyncMock(wraps=rig.graph.aupdate_state)


async def test_official_transcript_replaces_only_bound_session_group_without_generation(rig):
    await seed(rig)
    response = await read(rig)
    assert response.status_code == 200
    body = response.json()
    assert body["voice_sync_status"] == "synchronized"
    messages = body["messages"]
    assert [m["text"] for m in messages] == [
        "Texto antes",
        "Respuesta escrita antes",
        "Hola, soy Vita",
        "La misma frase",
        "Respuesta",
        "La misma frase",
        "Respuesta",
        "Texto después",
        "Respuesta escrita después",
    ]
    voice = [m for m in messages if m["channel"] == "voice"]
    assert [m["source_event_id"] for m in voice] == [1, 2, 2, 14, 14]
    assert len({m["message_id"] for m in voice}) == 5
    assert all(
        m["source"] == "elevenlabs_transcript" and m["status"] == "provider_final" for m in voice
    )
    assert all(m["voice_session_id"] == SID for m in voice)
    assert all(m["provider_message_id"] is None for m in voice)
    assert all(m["execution_id"] is None for m in voice)
    assert "SECRET" not in response.text and "BACKCHANNEL" not in response.text
    assert not rig.model.prompts
    snapshot = await rig.graph.aget_state(CONFIG)
    assert snapshot.next == ()
    assert len(snapshot.values["messages"]) == 9


async def test_replay_is_idempotent_correction_and_interruption_update_same_message(rig):
    await seed(rig)
    first = (await read(rig)).json()["messages"]
    second = (await read(rig)).json()["messages"]
    assert first == second
    assert rig.graph.aupdate_state.await_count == 1
    rig.provider.payload["transcript"][2].update(
        {
            "message": "Respuesta parcial",
            "interrupted": True,
            "original_message": "UNHEARD_COMPLETE_ORIGINAL_SECRET",
        }
    )
    corrected = await read(rig)
    row = corrected.json()["messages"][4]
    assert row["message_id"] == first[4]["message_id"]
    assert row["status"] == "interrupted" and row["revision"] == 2
    assert row["text"] == "Respuesta parcial"
    assert "UNHEARD_COMPLETE_ORIGINAL_SECRET" not in corrected.text
    assert (await read(rig)).json()["messages"] == corrected.json()["messages"]
    assert rig.graph.aupdate_state.await_count == 2
    assert not rig.model.prompts


@pytest.mark.parametrize("identity", ["message_id", "index"])
async def test_provider_message_id_and_old_api_index_fallback_are_stable(rig, identity):
    for index, row in enumerate(rig.provider.payload["transcript"]):
        row.pop("source_event_id", None)
        row["id"] = f"provider-{index}" if identity == "message_id" else None
    first = (await read(rig)).json()["messages"]
    assert len(first) == 5
    assert len({m["message_id"] for m in first}) == 5
    assert (await read(rig)).json()["messages"] == first
    assert not rig.model.prompts


@pytest.mark.parametrize("status", ["processing", "in-progress", "initiated"])
async def test_nonfinal_provider_is_retryable_and_does_not_replace_runtime(rig, status):
    await seed(rig)
    rig.provider.payload["status"] = status
    response = await read(rig)
    assert response.status_code == 200 and response.json()["voice_sync_status"] == "processing"
    assert "Generación que quizá no se oyó" in response.text
    rig.graph.aupdate_state.assert_not_awaited()
    rig.provider.payload["status"] = "done"
    assert (await read(rig)).json()["voice_sync_status"] == "synchronized"


async def test_processing_new_legacy_thread_returns_empty_retryable_state(rig):
    rig.provider.payload["status"] = "processing"
    response = await read(rig)
    assert response.status_code == 200
    assert (
        response.json()["messages"] == [] and response.json()["voice_sync_status"] == "processing"
    )
    assert not rig.model.prompts


@pytest.mark.parametrize(
    "failure", ["agent", "conversation", "failed", "provider_down", "malformed"]
)
async def test_provider_failure_or_wrong_binding_never_erases_existing_history(rig, failure):
    await seed(rig)
    if failure == "agent":
        rig.provider.payload["agent_id"] = "agent_other"
    elif failure == "conversation":
        rig.provider.payload["conversation_id"] = "conv_other"
    elif failure == "failed":
        rig.provider.payload["status"] = "failed"
    elif failure == "provider_down":
        rig.provider.code = 503
    else:
        rig.provider.payload = {"message": "arbitrary bot response"}
    response = await read(rig)
    assert response.status_code == 200 and response.json()["voice_sync_status"] == "unavailable"
    assert "Generación que quizá no se oyó" in response.text
    rig.graph.aupdate_state.assert_not_awaited()
    rig.store.mark_transcript_synced.assert_not_awaited()


async def test_other_owner_and_missing_auth_cannot_fetch_provider(rig):
    assert (await read(rig, owner="other")).status_code == 404
    assert (await read(rig, headers={})).status_code == 401
    assert not rig.provider.requests


async def test_revoked_consent_prevents_import(rig):
    await seed(rig)
    rig.consent.side_effect = VitaAccessError("vita_forbidden")
    assert (await read(rig)).status_code == 403
    assert not rig.provider.requests
    rig.graph.aupdate_state.assert_not_awaited()


@pytest.mark.parametrize("disabled", ["request", "flag", "session_active"])
async def test_sync_is_opt_in_and_requires_closed_session(rig, disabled):
    await seed(rig)
    if disabled == "flag":
        rig.monkeypatch.setattr(get_settings(), "vita_transcript_sync_enabled", False)
    elif disabled == "session_active":
        rig.store.record.revoked = False
    response = await read(rig, sync=disabled != "request")
    assert response.status_code == 200
    assert not rig.provider.requests
    rig.graph.aupdate_state.assert_not_awaited()


async def test_no_arbitrary_bot_import_from_client_body(rig):
    response = await read(
        rig,
        body={
            "conversation_id": "evil",
            "user_id": "other",
            "messages": [
                {"role": "bot", "text": "ARBITRARY_BOT_SECRET"},
            ],
        },
    )
    assert response.status_code == 200 and "ARBITRARY_BOT_SECRET" not in response.text
    assert len(rig.provider.requests) == 1
    assert rig.provider.requests[0].url.path.endswith("/conv_reserved")
    async with REAL_CLIENT(
        transport=httpx.ASGITransport(app=rig.app), base_url="http://ai.test"
    ) as client:
        assert (
            await client.post("/api/v1/threads/thread/state", json={"bot": "evil"})
        ).status_code == 405
    assert not rig.model.prompts


@pytest.mark.parametrize("mode", ["busy", "expired"])
async def test_lock_contention_is_retryable_and_expired_session_can_sync(rig, mode):
    await seed(rig)
    if mode == "busy":
        from app.vita.locking import ThreadBusyError

        @asynccontextmanager
        async def busy(_):
            raise ThreadBusyError
            yield

        rig.monkeypatch.setattr(transcript, "thread_guard", busy)
        assert (await read(rig)).json()["voice_sync_status"] == "busy"
        assert not rig.provider.requests
    else:
        rig.store.record.revoked = False
        rig.store.record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        assert (await read(rig)).json()["voice_sync_status"] == "synchronized"


@pytest.mark.parametrize("supports", [None, False])
async def test_old_client_stays_legacy_without_new_metadata_even_when_vita_flag_on(rig, supports):
    from app.voice import vita_session

    settings = get_settings()
    rig.monkeypatch.setattr(settings, "voice_enabled", True)
    rig.monkeypatch.setattr(settings, "vita_voice_enabled", True)
    rig.monkeypatch.setattr(settings, "voice_review_verification_enabled", False)
    rig.monkeypatch.setattr(settings, "elevenlabs_agent_id", "agent_legacy")
    rig.monkeypatch.setattr(
        "app.api.routes.voice.create_signed_url",
        AsyncMock(
            return_value={
                "signed_url": "wss://provider.test",
                "conversation_id": "conv_legacy",
            }
        ),
    )
    issuer = AsyncMock(side_effect=AssertionError("old client must not enter Vita"))
    rig.monkeypatch.setattr(vita_session, "create_vita_session", issuer)
    rig.monkeypatch.setattr(vita_session, "voice_store", rig.store)
    payload = {"user_id": "owner", "thread_id": "thread"}
    if supports is not None:
        payload["supports_vita_voice"] = supports
    async with REAL_CLIENT(
        transport=httpx.ASGITransport(app=rig.app), base_url="http://ai.test"
    ) as client:
        response = await client.post(
            "/internal/voice/session",
            json=payload,
            headers={"X-Internal-Key": settings.internal_api_key},
        )
    assert response.status_code == 200
    assert set(response.json()) == {"signed_url", "agent_id", "conversation_id"}
    issuer.assert_not_awaited()
    assert not rig.store.created


async def test_opted_in_legacy_client_gets_server_reserved_binding_without_context_token(rig):
    from app.voice import vita_session

    settings = get_settings()
    rig.monkeypatch.setattr(settings, "voice_enabled", True)
    rig.monkeypatch.setattr(settings, "vita_voice_enabled", False)
    rig.monkeypatch.setattr(settings, "voice_review_verification_enabled", False)
    rig.monkeypatch.setattr(settings, "elevenlabs_agent_id", "agent_legacy")
    rig.monkeypatch.setattr(
        "app.api.routes.voice.create_signed_url",
        AsyncMock(
            return_value={
                "signed_url": "wss://provider.test",
                "conversation_id": "conv_legacy",
            }
        ),
    )
    rig.monkeypatch.setattr(vita_session, "voice_store", rig.store)
    async with REAL_CLIENT(
        transport=httpx.ASGITransport(app=rig.app), base_url="http://ai.test"
    ) as client:
        response = await client.post(
            "/internal/voice/session",
            json={
                "user_id": "owner",
                "thread_id": "thread",
                "supports_vita_voice": True,
            },
            headers={"X-Internal-Key": settings.internal_api_key},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime_mode"] == "legacy" and payload["thread_id"] == "thread"
    assert "session_token" not in payload
    record = rig.store.created[0]
    assert payload["session_id"] == record.id
    assert record.agent_id == "agent_legacy" and record.conversation_id == "conv_legacy"
    assert record.user_id == "owner" and record.runtime_mode == "legacy"
