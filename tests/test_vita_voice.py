"""HTTP ASGI del adaptador con transporte/modelo/store simulados.

Estos tests no acreditan concurrencia PostgreSQL: el store SQL se verifica en
integración aislada antes de activar el modo. No se accede a la BD compartida.
"""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from pydantic import Field

from app.api.deps import get_db_session, get_graph
from app.api.main import create_app
from app.api.routes import vita_voice
from app.core.config import get_settings
from app.graph.graph import build_graph
from app.vita.runtime import ResolvedTurn
from app.voice.context import AUDIENCE, sign_context, verify_context
from app.voice.store import VoiceContextError
from tests.fakes import RecordingFakeModel
from tests.test_api import FakeDbSession

SECRET, SERVICE_KEY = "context-fixture-" * 3, "service-fixture-" * 3
SESSION_ID = "11111111-1111-1111-1111-111111111111"


class SimulatedStore:
    """Solo doble contractual, sin pretensión de representar locks entre réplicas."""

    def __init__(self):
        self.record = SimpleNamespace(
            id=SESSION_ID,
            user_id="owner",
            patient_id="patient",
            thread_id="shared",
            conversation_id="conv_fixture",
            agent_id="agent_vita_fixture",
            version_id=None,
            runtime_mode="vita",
            language="es",
            revoked=False,
            expires_at=datetime.fromtimestamp(int(time.time()) + 300, UTC),
        )
        self.receipts = {}

    async def get(self, sid):
        if sid != self.record.id or self.record.revoked:
            raise VoiceContextError("voice_session_inactive")
        return self.record

    async def start_turn(self, sid, ordinal, provider_turn, prefix, previous):
        await self.get(sid)
        if ordinal in self.receipts:
            turn = self.receipts[ordinal]
            if turn.prefix_hash != prefix or turn.provider_turn != provider_turn:
                raise VoiceContextError("voice_turn_conflict", 409)
            if turn.status != "completed":
                raise VoiceContextError("voice_turn_inflight_or_interrupted", 409)
            return turn
        latest = self.receipts.get(ordinal - 1)
        if ordinal != len(self.receipts) + 1 or (latest and latest.prefix_hash != previous):
            raise VoiceContextError("voice_history_sequence", 409)
        turn = SimpleNamespace(
            ordinal=ordinal,
            provider_turn=provider_turn,
            prefix_hash=prefix,
            status="running",
            response_message_id=f"voice:{sid}:{ordinal}:bot",
        )
        self.receipts[ordinal] = turn
        return turn

    async def finish_turn(self, sid, ordinal, status):
        self.receipts[ordinal].status = status

    async def revoke(self, sid, user_id):
        if sid != self.record.id or user_id != self.record.user_id:
            raise VoiceContextError("voice_session_not_found", 404)
        self.record.revoked = True


@pytest.fixture
def rig(monkeypatch):
    settings = get_settings()
    for name, value in {
        "voice_enabled": True,
        "vita_runtime_enabled": True,
        "vita_voice_enabled": True,
        "vita_voice_transport_verified": True,
        "vita_voice_service_key": SERVICE_KEY,
        "vita_voice_context_secret": SECRET,
    }.items():
        monkeypatch.setattr(settings, name, value)
    store = SimulatedStore()
    monkeypatch.setattr(vita_voice, "voice_store", store)
    monkeypatch.setattr("app.api.routes.voice.voice_store", store)
    monkeypatch.setattr(vita_voice, "read_catalog", AsyncMock(return_value=[]))
    history = SimpleNamespace(
        prepare_turn=AsyncMock(), before_turn_locked=AsyncMock(), ensure_locked=AsyncMock()
    )
    monkeypatch.setattr(vita_voice, "history_service", history)

    @asynccontextmanager
    async def unlocked(_):
        yield

    monkeypatch.setattr(vita_voice, "thread_guard", unlocked)
    model = RecordingFakeModel(
        responses=[
            AIMessage(content="Respuesta uno"),
            AIMessage(content="Respuesta dos"),
        ]
    )
    graph = build_graph(model=model, checkpointer=MemorySaver())

    async def resolve(**_):
        return ResolvedTurn(
            graph, "authorized-agent", "active-version", "fake", "fake", 25, "explicit"
        )

    resolver = AsyncMock(side_effect=resolve)
    monkeypatch.setattr(vita_voice, "resolve_turn", resolver)
    app = create_app()
    app.dependency_overrides[get_graph] = lambda: graph
    app.dependency_overrides[get_db_session] = FakeDbSession
    token = sign_context(SESSION_ID, int(store.record.expires_at.timestamp()), SECRET)
    headers = {
        "Authorization": f"Bearer {SERVICE_KEY}",
        "X-Vita-Session": token,
        "X-Vita-Conversation": "conv_fixture",
        "X-Vita-Turn": "1",
    }
    return SimpleNamespace(
        app=app,
        store=store,
        model=model,
        graph=graph,
        headers=headers,
        resolver=resolver,
        history=history,
        monkeypatch=monkeypatch,
    )


def body(*inputs, **extra):
    return {"stream": True, "messages": [{"role": "user", "content": t} for t in inputs], **extra}


async def post(rig, payload=None, headers=None):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=rig.app), base_url="http://test"
    ) as client:
        return await client.post(
            "/internal/voice/v1/chat/completions",
            json=payload or body("Hola"),
            headers=rig.headers if headers is None else headers,
        )


LANGUAGE_TOOLS = [{"type": "function", "function": {"name": "language_detection"}}]


async def test_app_language_is_not_assumed_to_be_the_providers_actual_language(rig):
    rig.store.record.language = "en"
    response = await post(rig, body("What is my assigned lunch?", tools=LANGUAGE_TOOLS))
    assert response.status_code == 200 and "language_detection" in response.text
    assert not rig.store.receipts
    rig.resolver.assert_not_awaited()


async def test_language_switch_cannot_emit_after_concurrent_revocation(rig):
    async def revoked_during_catalog(_):
        rig.store.record.revoked = True
        return []

    rig.monkeypatch.setattr(vita_voice, "read_catalog", revoked_during_catalog)
    response = await post(rig, body("What is my assigned lunch?", tools=LANGUAGE_TOOLS))
    assert response.status_code == 200
    assert "voice_turn_interrupted" in response.text and "language_detection" not in response.text
    assert not rig.store.receipts
    rig.resolver.assert_not_awaited()


async def test_history_cleanup_rejects_voice_before_generation(rig):
    rig.history.prepare_turn.side_effect = VoiceContextError("history_revision_changed", 409)
    result = await post(rig)
    assert result.status_code == 409
    rig.resolver.assert_not_awaited()
    assert rig.store.receipts[1].status == "interrupted"


async def test_replay_revalidates_history_before_sending_old_answer(rig):
    assert (await post(rig)).status_code == 200
    rig.history.before_turn_locked.side_effect = VoiceContextError("history_revision_changed", 409)
    replayed = await post(rig)
    assert replayed.status_code == 200
    assert "Respuesta uno" not in replayed.text
    assert "voice_turn_interrupted" in replayed.text
    assert len(rig.model.prompts) == 1


async def test_native_language_tool_roundtrip_is_not_another_patient_turn(rig):
    request = body("What does my nutrition plan include?", tools=LANGUAGE_TOOLS)
    switched = await post(rig, request)
    assert switched.status_code == 200
    chunks = [
        json.loads(line[6:]) for line in switched.text.splitlines() if line.startswith("data: {")
    ]
    call = chunks[0]["choices"][0]["delta"]["tool_calls"][0]
    assert call["function"]["name"] == "language_detection"
    assert json.loads(call["function"]["arguments"])["language"] == "en"
    assert chunks[1]["choices"][0]["finish_reason"] == "tool_calls"
    assert not rig.store.receipts
    rig.resolver.assert_not_awaited()
    request["messages"].extend(
        [
            {"role": "assistant", "content": None, "tool_calls": [call]},
            {"role": "tool", "content": "Language changed", "tool_call_id": call["id"]},
        ]
    )
    answered = await post(rig, request)
    assert answered.status_code == 200
    assert "Respuesta uno" in answered.text
    assert len(rig.store.receipts) == 1
    assert rig.resolver.call_args.kwargs["text"] == "What does my nutrition plan include?"
    assert len(rig.model.prompts) == 1


async def test_switch_back_to_spanish_and_neutral_words_preserve_language(rig):
    en_call = {
        "id": "en-call",
        "type": "function",
        "function": {
            "name": "language_detection",
            "arguments": '{"language":"en"}',
        },
    }
    request = body("What can you help me with?", tools=LANGUAGE_TOOLS)
    request["messages"].extend(
        [
            {"role": "assistant", "content": None, "tool_calls": [en_call]},
            {"role": "tool", "content": "Changed", "tool_call_id": "en-call"},
            {"role": "user", "content": "¿Qué dice mi plan nutricional?"},
        ]
    )
    switched = await post(rig, request)
    assert switched.status_code == 200
    assert "language_detection" in switched.text
    assert '\\"language\\": \\"es\\"' in switched.text
    request["messages"][-1]["content"] = "OK"
    # Neutral no cambia el idioma; el ordinal artificial no tiene recibo previo.
    # El rechazo de secuencia acredita que llegó al store y no emitió otra tool.
    neutral = await post(rig, request)
    assert neutral.status_code == 409
    assert "language_detection" not in neutral.text


async def test_only_language_tool_result_can_complete_a_provider_function_roundtrip(rig):
    request = body("Hello", tools=LANGUAGE_TOOLS)
    request["messages"].extend(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "x", "function": {"name": "get_patient_data"}}],
            },
            {"role": "tool", "content": "invented patient record", "tool_call_id": "x"},
        ]
    )
    result = await post(rig, request)
    assert result.status_code == 422
    rig.resolver.assert_not_awaited()


@pytest.mark.parametrize(
    "text,expected",
    [
        ('Using the document "Las heridas de la infancia", what are its main ideas?', "en"),
        ("According to 'Mapa de la conciencia', what does the author explain?", "en"),
        ("Responde en inglés", "en"),
        ("Respóndeme en inglés", "en"),
        ("Contéstame en inglés", "en"),
        ("Please speak in Spanish", "es"),
    ],
)
def test_document_titles_and_explicit_preferences_do_not_break_bilingual_voice(text, expected):
    from app.graph.language import detect_turn_language

    assert detect_turn_language({"input": text}) == expected


@pytest.mark.parametrize(
    "change", ["tamper", "expired", "wrong_conversation", "revoked", "wrong_service"]
)
async def test_invalid_context_is_rejected_before_runtime(rig, change):
    headers = dict(rig.headers)
    if change == "tamper":
        parts = headers["X-Vita-Session"].split(".")
        parts[1] = ("A" if parts[1][0] != "A" else "B") + parts[1][1:]
        headers["X-Vita-Session"] = ".".join(parts)
    elif change == "expired":
        headers["X-Vita-Session"] = sign_context(SESSION_ID, int(time.time()) - 1, SECRET)
    elif change == "wrong_conversation":
        headers["X-Vita-Conversation"] = "conv_other"
    elif change == "revoked":
        rig.store.record.revoked = True
    else:
        headers["Authorization"] = "Bearer wrong"
    response = await post(rig, headers=headers)
    assert response.status_code in (401, 403)
    rig.resolver.assert_not_awaited()
    assert not rig.model.prompts


@pytest.mark.parametrize(
    "header", ["Authorization", "X-Vita-Session", "X-Vita-Conversation", "X-Vita-Turn"]
)
async def test_missing_headers_never_generate(rig, header):
    headers = {key: value for key, value in rig.headers.items() if key != header}
    assert (await post(rig, headers=headers)).status_code in (401, 422)
    rig.resolver.assert_not_awaited()


async def test_replay_does_not_regenerate_and_equal_phrases_at_different_ordinals_are_distinct(rig):
    first = await post(rig, body("La misma frase"))
    replay = await post(rig, body("La misma frase"))
    assert first.status_code == replay.status_code == 200
    assert "Respuesta uno" in first.text and "Respuesta uno" in replay.text
    assert len(rig.model.prompts) == 1
    second = await post(
        rig, body("La misma frase", "La misma frase"), headers={**rig.headers, "X-Vita-Turn": "2"}
    )
    assert second.status_code == 200 and "Respuesta dos" in second.text
    assert len(rig.model.prompts) == 2
    snapshot = await rig.graph.aget_state({"configurable": {"thread_id": "owner::shared"}})
    users = [m for m in snapshot.values["messages"] if m.type == "human"]
    assert len(users) == 2 and users[0].content == users[1].content
    assert users[0].id != users[1].id
    assert first.text.endswith("data: [DONE]\n\n")


async def test_conflicting_replay_is_not_a_new_question(rig):
    await post(rig)
    assert (await post(rig, body("Texto cambiado"))).status_code == 409
    assert len(rig.model.prompts) == 1


@pytest.mark.parametrize("field", ["user_id", "userId", "patientId", "thread_id", "session_token"])
async def test_body_cannot_change_signed_identity(rig, field):
    assert (await post(rig, body("Hola", **{field: "other"}))).status_code == 422
    rig.resolver.assert_not_awaited()


async def test_only_final_visible_text_leaves_adapter(rig):
    rig.model.responses = [
        AIMessage(
            content="INTERNAL_TOOL_PLANNING",
            tool_calls=[
                {
                    "name": "calculate",
                    "args": {"expression": "2+2"},
                    "id": "math",
                }
            ],
        ),
        AIMessage(
            content=[
                {"type": "thinking", "thinking": "SECRET_REASONING"},
                {"type": "text", "text": "El resultado es cuatro"},
            ]
        ),
    ]
    request = body("Cuánto es dos más dos", model="evil-model", tools=[{"name": "write_patient"}])
    request["messages"].insert(0, {"role": "system", "content": "MALICIOUS_SYSTEM"})
    response = await post(rig, request)
    assert response.status_code == 200
    assert "El resultado es cuatro" in response.text
    assert not any(
        value in response.text
        for value in (
            "INTERNAL_TOOL_PLANNING",
            "SECRET_REASONING",
            "MALICIOUS_SYSTEM",
            "tool_calls",
            SECRET,
            SERVICE_KEY,
            rig.headers["X-Vita-Session"],
        )
    )
    assert all("MALICIOUS_SYSTEM" not in m.content for prompt in rig.model.prompts for m in prompt)


async def test_http_asgi_cancellation_cancels_tool_and_marks_receipt_interrupted(rig):
    started, cancelled = asyncio.Event(), asyncio.Event()

    @tool
    async def slow() -> str:
        """Lectura lenta cancelable."""
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    rig.model.responses = [
        AIMessage(content="", tool_calls=[{"name": "slow", "args": {}, "id": "slow"}])
    ]
    rig.graph = build_graph(model=rig.model, tools=[slow], checkpointer=MemorySaver())
    rig.resolver.side_effect = None
    rig.resolver.return_value = ResolvedTurn(rig.graph, "a", "v", "fake", "fake", 25, "explicit")
    task = asyncio.create_task(post(rig, body("Consulta lenta")))
    await asyncio.wait_for(started.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(cancelled.wait(), 2)
    assert rig.store.receipts[1].status == "interrupted"


async def test_revoke_requires_owner_and_invalidates_token(rig):
    headers = {"X-Internal-Key": get_settings().internal_api_key}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=rig.app), base_url="http://test"
    ) as client:
        assert (
            await client.delete(
                f"/internal/voice/session/{SESSION_ID}?user_id=other", headers=headers
            )
        ).status_code == 404
        assert (
            await client.delete(
                f"/internal/voice/session/{SESSION_ID}?user_id=owner", headers=headers
            )
        ).status_code == 204
        assert (
            await client.delete(
                f"/internal/voice/session/{SESSION_ID}?user_id=owner", headers=headers
            )
        ).status_code == 204
    assert (await post(rig)).status_code == 403


def test_token_has_no_patient_identity_and_binds_audience():
    expiry = int(time.time()) + 60
    token = sign_context(SESSION_ID, expiry, SECRET)
    assert verify_context(token, SECRET) == (SESSION_ID, expiry)
    import base64

    claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
    assert claims == {"sid": SESSION_ID, "exp": expiry, "aud": AUDIENCE}


@pytest.mark.parametrize(
    "flag",
    [
        "voice_enabled",
        "vita_runtime_enabled",
        "vita_voice_enabled",
        "vita_voice_transport_verified",
    ],
)
async def test_each_disabled_flag_closes_adapter(rig, flag):
    rig.monkeypatch.setattr(get_settings(), flag, False)
    assert (await post(rig)).status_code == 503
    rig.resolver.assert_not_awaited()


async def test_vita_session_emits_only_reduced_context_and_bound_thread(rig):
    from app.voice import vita_session

    settings = get_settings()
    rig.monkeypatch.setattr(settings, "vita_elevenlabs_agent_id", "agent_vita_fixture")
    rig.monkeypatch.setattr(settings, "vita_elevenlabs_version_id", "agtvrsn_vita_fixture")
    rig.monkeypatch.setattr(vita_session, "read_catalog", AsyncMock(return_value=[]))
    upstream = AsyncMock(
        return_value={"signed_url": "wss://fixture.test/session", "conversation_id": "conv_bound"}
    )
    saved = AsyncMock()
    rig.monkeypatch.setattr(vita_session, "create_signed_url", upstream)
    rig.monkeypatch.setattr(vita_session, "voice_store", SimpleNamespace(create=saved))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=rig.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/internal/voice/session",
            json={
                "user_id": "owner",
                "patient_id": "patient",
                "thread_id": "existing",
                "language": "en",
                "supports_vita_voice": True,
            },
            headers={"X-Internal-Key": settings.internal_api_key},
        )
    assert response.status_code == 200
    result = response.json()
    assert result["runtime_mode"] == "vita" and result["thread_id"] == "existing"
    assert result["conversation_id"] == "conv_bound"
    sid, expiry = verify_context(result["session_token"], SECRET)
    record = saved.call_args.args[0]
    assert sid == record.id and record.user_id == "owner"
    assert record.thread_id == "existing" and int(record.expires_at.timestamp()) == expiry
    assert "owner" not in result["session_token"] and "patient" not in result["session_token"]
    assert len(upstream.call_args.args) == 3  # No JWT/identidad enviados al proveedor.


async def test_revocation_during_http_asgi_generation_cancels_tool(rig):
    started, cancelled = asyncio.Event(), asyncio.Event()

    @tool
    async def slow() -> str:
        """Lectura aún en vuelo al revocar desde otra réplica."""
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    rig.model.responses = [
        AIMessage(content="", tool_calls=[{"name": "slow", "args": {}, "id": "slow"}])
    ]
    graph = build_graph(model=rig.model, tools=[slow], checkpointer=MemorySaver())
    rig.resolver.side_effect = None
    rig.resolver.return_value = ResolvedTurn(graph, "a", "v", "fake", "fake", 25, "explicit")
    task = asyncio.create_task(post(rig, body("Consulta lenta")))
    await asyncio.wait_for(started.wait(), 3)
    rig.store.record.revoked = True
    response = await asyncio.wait_for(task, 3)
    assert "voice_turn_interrupted" in response.text
    assert cancelled.is_set()
    assert rig.store.receipts[1].status == "interrupted"


class StreamingFinalModel(RecordingFakeModel):
    release: Any = Field(default_factory=asyncio.Event)
    generation_complete: Any = Field(default_factory=asyncio.Event)

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        self.prompts.append(list(messages))
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content=[
                    {
                        "type": "thinking",
                        "thinking": "SECRET_REASONING",
                    }
                ]
            )
        )
        yield ChatGenerationChunk(message=AIMessageChunk(content="Primero "))
        await self.release.wait()
        yield ChatGenerationChunk(message=AIMessageChunk(content="después"))
        self.generation_complete.set()


async def test_http_asgi_first_final_token_precedes_generation_end_without_planning(rig):
    @tool
    def internal_read() -> str:
        """Lectura interna preparada en código, nunca narrada al proveedor."""
        return "INTERNAL_TOOL_PLANNING"

    model = StreamingFinalModel(responses=[AIMessage(content="unused")])
    graph = build_graph(
        model=model,
        tools=[internal_read],
        prepare_tools=["internal_read"],
        checkpointer=MemorySaver(),
    )
    rig.resolver.side_effect = None
    rig.resolver.return_value = ResolvedTurn(graph, "a", "v", "fake", "fake", 25, "explicit")
    first_content = asyncio.Event()
    bodies = []
    request_sent = False
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "method": "POST",
        "scheme": "http",
        "path": "/internal/voice/v1/chat/completions",
        "raw_path": b"/internal/voice/v1/chat/completions",
        "query_string": b"",
        "http_version": "1.1",
        "server": ("test", 80),
        "client": ("test", 123),
        "headers": [
            (k.lower().encode(), v.encode())
            for k, v in {
                **rig.headers,
                "Content-Type": "application/json",
            }.items()
        ],
    }

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {
                "type": "http.request",
                "body": json.dumps(body("Consulta sintética")).encode(),
                "more_body": False,
            }
        await asyncio.Event().wait()

    async def send(message):
        if message["type"] == "http.response.body":
            bodies.append(message.get("body", b""))
            if b"Primero" in bodies[-1]:
                first_content.set()

    task = asyncio.create_task(rig.app(scope, receive, send))
    try:
        await asyncio.wait_for(first_content.wait(), 3)
        assert not model.generation_complete.is_set()
        assert not task.done()
    finally:
        model.release.set()
        await asyncio.wait_for(task, 3)
    response = b"".join(bodies).decode()
    assert "Primero" in response and "después" in response
    assert "INTERNAL_TOOL_PLANNING" not in response and "SECRET_REASONING" not in response
    assert len(model.prompts) == 1  # Sin generación de planificación ni reescritor.
    assert rig.store.receipts[1].status == "completed"
