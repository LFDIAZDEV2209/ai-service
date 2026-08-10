"""Rutas de chat: respuesta completa y streaming SSE."""

from __future__ import annotations

import json
import threading
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AIMessageChunk

from app.api.deps import get_graph
from app.api.schemas import ChatRequest, ChatResponse
from app.core.config import get_settings
from app.core.errors import CoppAiError
from app.core.logging import get_logger
from app.safety.guardrails import RateLimiter

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

_rate_limiters: dict[str, RateLimiter] = {}
_rate_limiters_lock = threading.Lock()


_MAX_ACTIVE_LIMITERS = 10_000


def _prune_inactive_limiters() -> None:
    """Evita la fuga de memoria: elimina limiters sin actividad reciente cuando
    el dict supera el umbral."""
    if len(_rate_limiters) < _MAX_ACTIVE_LIMITERS:
        return
    for key, limiter in list(_rate_limiters.items()):
        if limiter.remaining == limiter.max_requests:  # sin actividad en la ventana
            del _rate_limiters[key]


def _rate_limiter_for(client_key: str) -> RateLimiter:
    with _rate_limiters_lock:
        _prune_inactive_limiters()
        limiter = _rate_limiters.get(client_key)
        if limiter is None:
            limiter = RateLimiter()
            _rate_limiters[client_key] = limiter
        return limiter


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_rate_limit(request: Request) -> None:
    if not _rate_limiter_for(_client_key(request)).check():
        raise HTTPException(
            status_code=429,
            detail="Demasiadas solicitudes. Espera un momento antes de continuar.",
        )


def _build_config(thread_id: str) -> dict:
    settings = get_settings()
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": settings.recursion_limit,
    }


def _extract_answer(state: dict) -> str:
    messages = state.get("messages", [])
    if not messages:
        return ""
    last = messages[-1]
    content = getattr(last, "content", "")
    return content if isinstance(content, str) else str(content)


@router.post("", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    http_request: Request,
    graph=Depends(get_graph),
) -> ChatResponse:
    """Ejecuta el agente de principio a fin y devuelve la respuesta completa."""
    _check_rate_limit(http_request)
    thread_id = request.thread_id or str(uuid.uuid4())

    try:
        result = await graph.ainvoke(
            {"input": request.message, "agent": request.agent},
            config=_build_config(thread_id),
        )
    except CoppAiError as exc:
        logger.exception("Error ejecutando el grafo")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ChatResponse(
        thread_id=thread_id,
        answer=_extract_answer(result),
        agent=request.agent,
        tools_used=list(result.get("tools_used", [])),
        model=result.get("provider"),
    )


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    http_request: Request,
    graph=Depends(get_graph),
) -> StreamingResponse:
    """Streaming del agente vía Server-Sent Events (tokens + nodos en vivo)."""
    _check_rate_limit(http_request)
    thread_id = request.thread_id or str(uuid.uuid4())

    async def event_generator():
        yield "event: start\ndata: {}\n\n"
        try:
            async for mode, chunk in graph.astream(
                {"input": request.message, "agent": request.agent},
                config=_build_config(thread_id),
                stream_mode=["updates", "messages"],
            ):
                if mode == "messages":
                    message_chunk, _metadata = chunk
                    # Solo transmitir tokens de AIMessage/AIMessageChunk, no HumanMessage
                    if not isinstance(message_chunk, (AIMessage, AIMessageChunk)):
                        continue
                    content = getattr(message_chunk, "content", "")
                    # Claude devuelve content como lista de bloques, extraer texto
                    if isinstance(content, list):
                        text = "".join(
                            block.get("text", "")
                            for block in content
                            if isinstance(block, dict) and block.get("type") == "text"
                        )
                    else:
                        text = content if isinstance(content, str) else ""
                    if text:
                        payload = json.dumps(
                            {"type": "token", "content": text},
                            ensure_ascii=False,
                        )
                        yield f"event: message\ndata: {payload}\n\n"
                elif mode == "updates":
                    for node_name in chunk:
                        payload = json.dumps({"type": "node", "node": node_name})
                        yield f"event: node\ndata: {payload}\n\n"
        except Exception as exc:
            logger.exception("Error en el stream del agente")
            payload = json.dumps({"type": "error", "error": str(exc)}, ensure_ascii=False)
            yield f"event: error\ndata: {payload}\n\n"
        yield f"event: done\ndata: {json.dumps({'thread_id': thread_id})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
