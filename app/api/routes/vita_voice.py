"""Custom LLM detrás de BACK: cabeceras ligadas a sesión, SSE sin prompts internos."""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from contextlib import suppress
from typing import Literal

from anyio import CancelScope
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AIMessageChunk
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.deps import get_db_session, get_graph
from app.api.message_text import extract_message_text
from app.api.routes.chat import _build_config, _check_rate_limit, _extract_answer
from app.api.schemas import ChatRequest
from app.core.config import get_settings
from app.history.service import history_service, storage_key
from app.vita.client import VitaAccessError, read_catalog
from app.vita.locking import ThreadBusyError, thread_guard
from app.vita.runtime import resolve_turn
from app.voice.context import verify_context
from app.voice.language import (
    conversation_language,
    language_chunk,
    language_switch,
    latest_user_index,
    valid_language_followup,
)
from app.voice.store import VoiceContextError, voice_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/internal/voice/v1", tags=["voice-runtime"])


class ProviderMessage(BaseModel):
    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: str | None = Field(default=None, max_length=8000)
    tool_calls: list[dict] = Field(default_factory=list, max_length=4)
    tool_call_id: str | None = Field(default=None, max_length=200)


class CompletionRequest(BaseModel):
    # Campos OpenAI del proveedor se ignoran: jamás sobreescriben modelo/tools/KB/prompt.
    model_config = ConfigDict(extra="ignore")
    stream: Literal[True]
    messages: list[ProviderMessage] = Field(min_length=1, max_length=128)
    tools: list[dict] = Field(default_factory=list, max_length=16)

    @model_validator(mode="before")
    @classmethod
    def reject_identity(cls, value):
        if isinstance(value, dict) and any(
            key in value
            for key in (
                "user_id",
                "userId",
                "patient_id",
                "patientId",
                "thread_id",
                "threadId",
                "session_token",
                "sessionToken",
                "conversation_id",
                "conversationId",
            )
        ):
            raise ValueError("identity_must_use_signed_session_headers")
        return value

    @model_validator(mode="after")
    def validate_history(self):
        index = latest_user_index(self.messages)
        if index < 0 or not (self.messages[index].content or "").strip():
            raise ValueError("last_user_message_required")
        if self.messages[-1].role != "user" and not valid_language_followup(self.messages):
            raise ValueError("last_user_message_required")
        if sum(len(m.content or "") for m in self.messages) > 64_000:
            raise ValueError("history_limit")
        return self


def history_identity(messages: list[ProviderMessage]) -> tuple[int, str, str]:
    inputs = [m.content or "" for m in messages if m.role == "user"]

    def digest(values):
        return hashlib.sha256(
            json.dumps(
                values,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    return len(inputs), digest(inputs), digest(inputs[:-1])


def require_voice_service(authorization: str | None = Header(default=None)) -> None:
    settings = get_settings()
    if (
        not settings.voice_enabled
        or not settings.vita_runtime_enabled
        or not settings.vita_voice_enabled
        or not settings.vita_voice_transport_verified
        or len(settings.vita_voice_service_key.encode()) < 32
    ):
        raise HTTPException(status_code=503, detail="vita_voice_disabled_or_unverified")
    expected = f"Bearer {settings.vita_voice_service_key}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="voice_service_auth_required")


def _chunk(response_id: str, content: str | None = None, *, done=False) -> str:
    payload = {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "vita",
        "choices": [
            {
                "index": 0,
                "delta": {"content": content} if content is not None else {},
                "finish_reason": "stop" if done else None,
            }
        ],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/chat/completions", dependencies=[Depends(require_voice_service)])
async def completion(
    payload: CompletionRequest,
    http_request: Request,
    x_vita_session: str = Header(min_length=1),
    x_vita_conversation: str = Header(min_length=1, max_length=200),
    x_vita_turn: int = Header(ge=1, le=1000000),
    session=Depends(get_db_session),
    base_graph=Depends(get_graph),
) -> StreamingResponse:
    request_started = time.perf_counter()
    settings = get_settings()
    try:
        session_id, expiry = verify_context(x_vita_session, settings.vita_voice_context_secret)
        record = await voice_store.get(session_id)
        if (
            record.runtime_mode != "vita"
            or record.conversation_id != x_vita_conversation
            or int(record.expires_at.timestamp()) != expiry
        ):
            raise VoiceContextError("voice_context_binding")
        await read_catalog(record.user_id)
        _check_rate_limit(http_request, record.user_id)
        ordinal, prefix, previous = history_identity(payload.messages)
        switch = language_switch(payload.messages, payload.tools)
        if switch:

            async def switch_language():
                response_id = f"voice:{session_id}:{ordinal}"
                data = {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": "vita",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"tool_calls": [language_chunk(response_id, switch)]},
                            "finish_reason": None,
                        }
                    ],
                }
                try:
                    async with thread_guard(storage_key(record.user_id, record.thread_id)):
                        await history_service.ensure_locked(
                            record.user_id, record.thread_id, base_graph.checkpointer
                        )
                        await voice_store.get(session_id)
                        yield f"data: {json.dumps(data)}\n\n"
                        data["choices"] = [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]
                        yield f"data: {json.dumps(data)}\n\n"
                        yield "data: [DONE]\n\n"
                except (VoiceContextError, ThreadBusyError):
                    yield 'data: {"error":{"code":"voice_turn_interrupted"}}\n\n'

            return StreamingResponse(
                switch_language(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-store"},
            )
        receipt = await voice_store.start_turn(session_id, ordinal, x_vita_turn, prefix, previous)
    except VoiceContextError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except VitaAccessError as exc:
        raise HTTPException(status_code=403, detail="voice_access_unavailable") from exc

    request = ChatRequest(
        message=payload.messages[latest_user_index(payload.messages)].content,
        user_id=record.user_id,
        patient_id=record.patient_id,
        thread_id=record.thread_id,
        language=conversation_language(payload.messages, record.language),
    )
    config = _build_config(record.thread_id, request)
    config["configurable"].update(
        {
            "user_message_id": f"voice:{session_id}:{ordinal}:user",
            "assistant_message_id": receipt.response_message_id,
            "message_channel": "voice",
            "voice_session_id": record.id,
        }
    )
    try:
        await history_service.prepare_turn(
            record.user_id, record.thread_id, base_graph.checkpointer, config
        )
    except (VoiceContextError, ThreadBusyError) as exc:
        if receipt.status != "completed":
            with suppress(VoiceContextError):
                await voice_store.finish_turn(session_id, ordinal, "interrupted")
        raise HTTPException(status_code=getattr(exc, "status", 409), detail=str(exc)) from exc
    except Exception as exc:
        with suppress(VoiceContextError):
            await voice_store.finish_turn(session_id, ordinal, "interrupted")
        raise HTTPException(status_code=503, detail="history_state_unavailable") from exc
    if receipt.status == "completed":
        snapshot = await base_graph.aget_state(config)
        message = next(
            (
                m
                for m in (snapshot.values or {}).get("messages", [])
                if m.id == receipt.response_message_id
            ),
            None,
        )
        if message is None:
            raise HTTPException(status_code=409, detail="voice_receipt_without_checkpoint")

        async def replay():
            try:
                async with thread_guard(config["configurable"]["thread_id"]):
                    await history_service.before_turn_locked(base_graph, config)
                    current = await base_graph.aget_state(config)
                    replay_message = next(
                        (
                            m
                            for m in (current.values or {}).get("messages", [])
                            if m.id == receipt.response_message_id
                        ),
                        None,
                    )
                    if replay_message is None:
                        raise VoiceContextError("voice_receipt_without_checkpoint", 409)
                    yield _chunk(
                        receipt.response_message_id, extract_message_text(replay_message.content)
                    )
                    yield _chunk(receipt.response_message_id, done=True)
                    yield "data: [DONE]\n\n"
            except (VoiceContextError, ThreadBusyError):
                yield 'data: {"error":{"code":"voice_turn_interrupted"}}\n\n'

        return StreamingResponse(replay(), media_type="text/event-stream")
    try:
        turn = await resolve_turn(
            user_id=record.user_id,
            text=request.message,
            session=session,
            base_graph=base_graph,
            config=config,
            channel="voice",
        )
        config["recursion_limit"] = turn.recursion_limit
    except Exception as exc:
        await voice_store.finish_turn(session_id, ordinal, "interrupted")
        raise HTTPException(status_code=503, detail="voice_runtime_unavailable") from exc

    async def events():
        started = request_started
        first_token_ms = None
        state, completed = None, False
        next_access_check = time.monotonic() + 1
        stream = turn.graph.astream(
            {"input": request.message, "agent": turn.agent_id},
            config=config,
            stream_mode=["messages", "values"],
        )
        pending = None
        try:
            async with asyncio.timeout(settings.vita_turn_timeout):
                while True:
                    pending = asyncio.create_task(anext(stream))
                    while not pending.done():
                        await asyncio.wait({pending}, timeout=0.25)
                        # Lectura durable en otra conexión: la revocación de otra
                        # réplica cancela hasta una tool/LLM que aún no emite tokens.
                        await voice_store.get(session_id)
                        if time.monotonic() >= next_access_check:
                            await read_catalog(record.user_id)
                            next_access_check = time.monotonic() + 1
                    try:
                        mode, chunk = pending.result()
                    except StopAsyncIteration:
                        break
                    if mode == "values":
                        state = chunk
                    elif mode == "messages":
                        message, metadata = chunk
                        if (
                            metadata.get("langgraph_node") == "answer"
                            and isinstance(message, (AIMessage, AIMessageChunk))
                            and not getattr(message, "tool_calls", None)
                            and not getattr(message, "tool_call_chunks", None)
                        ):
                            text = extract_message_text(message.content)
                            if not text:
                                continue
                            if first_token_ms is None:
                                first_token_ms = int((time.perf_counter() - started) * 1000)
                            yield _chunk(receipt.response_message_id, text)
                if state is None:
                    raise VoiceContextError("voice_stream_incomplete", 502)
                if first_token_ms is None:
                    first_token_ms = int((time.perf_counter() - started) * 1000)
                    yield _chunk(receipt.response_message_id, _extract_answer(state))
                await voice_store.get(session_id)
                await voice_store.finish_turn(session_id, ordinal, "completed")
                completed = True
                yield _chunk(receipt.response_message_id, done=True)
                yield "data: [DONE]\n\n"
        except (VoiceContextError, VitaAccessError, TimeoutError, ThreadBusyError):
            yield 'data: {"error":{"code":"voice_turn_interrupted"}}\n\n'
        except asyncio.CancelledError:
            raise
        except Exception:
            # Nunca serializar errores del proveedor/modelo: pueden contener PHI.
            yield 'data: {"error":{"code":"voice_runtime_error"}}\n\n'
        finally:
            # Starlette cancela dentro de un CancelScope. Sin shield, ese scope
            # puede interrumpir la limpieza antes de propagar la cancelación a
            # la tool y cerrar el stream/checkpoint.
            with CancelScope(shield=True):
                if pending and not pending.done():
                    pending.cancel()
                    with suppress(asyncio.CancelledError, StopAsyncIteration):
                        await pending
                with suppress(Exception):
                    await stream.aclose()
                if not completed:
                    await voice_store.finish_turn(session_id, ordinal, "interrupted")
            logger.info(
                "vita_voice_turn agent=%s version=%s reason=%s "
                "first_token_ms=%s duration_ms=%s status=%s tools=%s source_count=%s language=%s",
                turn.agent_id,
                turn.version_id,
                turn.reason,
                first_token_ms,
                int((time.perf_counter() - started) * 1000),
                "generated" if completed else "interrupted",
                (state or {}).get("tools_used", []),
                len((state or {}).get("data_sources", [])),
                request.language,
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )
