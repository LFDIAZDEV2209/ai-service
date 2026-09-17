"""Rutas de chat: respuesta completa y streaming SSE."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, AIMessageChunk

from app.agents.runtime_registry import AgentRuntimeError
from app.agents.runtime_registry import registry as runtime_registry
from app.api.deps import get_db_session, get_graph
from app.api.message_text import extract_message_text
from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    ChatSuggestion,
    FeedbackRequest,
    FeedbackResponse,
)
from app.api.security import require_internal_key
from app.core.config import get_settings
from app.core.errors import CoppAiError
from app.core.logging import get_logger
from app.memory.adaptive import AdaptiveMemoryService
from app.observability.executions import ExecutionTracker
from app.safety.guardrails import RateLimiter

logger = get_logger(__name__)

# Canal interno: solo el backend .NET (con X-Internal-Key) puede invocar el
# chat. El frontend NUNCA llama a este servicio directo.
router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(require_internal_key)])

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


def _client_key(request: Request, user_id: str | None) -> str:
    """Clave de rate limit: el `user_id` real (inyectado por el backend desde
    el JWT) cuando existe; si no, la IP como fallback (p. ej. health/uso sin
    identidad). Varios usuarios tras una misma IP no se penalizan entre sí."""
    if user_id:
        return f"user:{user_id}"
    return f"ip:{request.client.host}" if request.client else "ip:unknown"


def _check_rate_limit(request: Request, user_id: str | None) -> None:
    if not _rate_limiter_for(_client_key(request, user_id)).check():
        raise HTTPException(
            status_code=429,
            detail="Demasiadas solicitudes. Espera un momento antes de continuar.",
        )


def _storage_thread_id(user_id: str | None, thread_id: str) -> str:
    """Clave interna del thread en el checkpointer.

    Se antepone el `user_id` real (proviene del backend, que lo deriva del
    JWT): aunque dos usuarios enviaran el mismo `thread_id` desde el cliente,
    jamás comparten historial ni estado — aislamiento de memoria/threads
    garantizado a nivel de almacenamiento, no solo de UI.
    """
    if user_id:
        return f"{user_id}::{thread_id}"
    return thread_id


def _build_config(
    thread_id: str,
    request: ChatRequest | None = None,
    recursion_limit: int | None = None,
) -> dict:
    settings = get_settings()
    storage_thread_id = _storage_thread_id(
        request.user_id if request is not None else None,
        thread_id,
    )
    configurable = {"thread_id": storage_thread_id}

    # Aislamiento multi-agente: el checkpointer y las tools separan el estado
    # por usuario/paciente/instancia además del thread.
    if request is not None:
        if request.user_id:
            configurable["user_id"] = request.user_id
        if request.patient_id:
            configurable["patient_id"] = request.patient_id
        if request.agent_instance_id:
            configurable["agent_instance_id"] = request.agent_instance_id
        # Contexto del control de programa (backend .NET): viaja por config y
        # NUNCA persiste en el checkpointer — es guía de UN turno. None cuando
        # el turno no corresponde a un control (comportamiento byte-idéntico).
        configurable["control_context"] = (
            request.control_context.model_dump() if request.control_context is not None else None
        )

    return {
        "configurable": configurable,
        "recursion_limit": recursion_limit or settings.recursion_limit,
    }


async def _resolve_graph(
    request: ChatRequest,
    session,
) -> tuple[Any, int, str | None, str | None, str | None]:
    """Resuelve el grafo a ejecutar.

    Si `agent_type_id` viene, usa el runtime multi-agente (compilado/cacheado
    por el registry) y su recursion_limit configurado; si no, el grafo base
    (perfil `agent`).

    Returns:
        (graph, recursion_limit, version_id, provider, model)
    """
    if request.agent_type_id:
        try:
            compiled = await runtime_registry.get_agent(request.agent_type_id, session)
            cfg = compiled.runtime_config
            return (
                compiled.graph,
                compiled.runtime_config.recursion_limit,
                compiled.version_id,
                cfg.provider,
                cfg.model,
            )
        except AgentRuntimeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return None, get_settings().recursion_limit, None, None, None


def _extract_answer(state: dict) -> str:
    messages = state.get("messages", [])
    if not messages:
        return ""
    last = messages[-1]
    return extract_message_text(getattr(last, "content", ""))


def _normalize_suggestions(raw: Any) -> list[ChatSuggestion]:
    """Normaliza las sugerencias crudas del estado a `ChatSuggestion`.

    Las entradas malformadas se descartan de forma defensiva (nunca deben
    tumbar la respuesta del chat ni el evento done del stream).
    """
    suggestions: list[ChatSuggestion] = []
    if not isinstance(raw, list):
        return suggestions
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            suggestions.append(ChatSuggestion.model_validate(item))
        except Exception:
            logger.debug("Sugerencia de chat descartada (malformada): %s", item)
    return suggestions


@router.post("", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    http_request: Request,
    session=Depends(get_db_session),
    base_graph=Depends(get_graph),
) -> ChatResponse:
    """Ejecuta el agente de principio a fin y devuelve la respuesta completa."""
    _check_rate_limit(http_request, request.user_id)
    thread_id = request.thread_id or str(uuid.uuid4())

    # Correlation ID propagado por el backend (X-Correlation-ID) para trazar
    # la cadena Frontend → Backend → AI en los logs.
    correlation_id = http_request.headers.get("x-correlation-id")
    if correlation_id:
        logger.info(
            "chat: correlation_id=%s user=%s thread=%s agent=%s",
            correlation_id,
            request.user_id,
            thread_id,
            request.agent_type_id or request.agent or "base",
        )

    graph, recursion_limit, version_id, provider, model = await _resolve_graph(request, session)
    if graph is None:
        graph = base_graph

    tracker = ExecutionTracker(session)
    execution_id = await tracker.start(
        thread_id=thread_id,
        agent_type_id=request.agent_type_id or request.agent or "base",
        version_id=version_id,
        agent_instance_id=request.agent_instance_id,
        user_id=request.user_id,
        provider=provider,
        model=model,
        message=request.message,
    )
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            graph.ainvoke(
                {
                    "input": request.message,
                    "agent": request.agent or request.agent_type_id or "base",
                },
                config=_build_config(thread_id, request, recursion_limit),
            ),
            timeout=get_settings().llm_invoke_timeout,
        )
        await tracker.complete(
            execution_id,
            result_state=result,
            model=model,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    except CoppAiError as exc:
        logger.exception("Error ejecutando el grafo")
        await tracker.fail(
            execution_id,
            error=str(exc),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        await session.commit()
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except TimeoutError:
        logger.error("Timeout del agente (thread=%s)", thread_id)
        await tracker.fail(
            execution_id,
            error="Timeout del agente",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        await session.commit()
        raise HTTPException(
            status_code=500, detail="No fue posible procesar la solicitud."
        ) from None
    except Exception as exc:
        # Cualquier error no tipado (p. ej. fallo de la API del LLM) debe
        # actualizar el estado de la ejecución y devolver un mensaje seguro,
        # no dejar la execution en RUNNING para siempre.
        logger.exception("Error no controlado ejecutando el grafo")
        await tracker.fail(
            execution_id,
            error=str(exc),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        await session.commit()
        raise HTTPException(
            status_code=500, detail="No fue posible procesar la solicitud."
        ) from exc

    await session.commit()

    tools_used = list(result.get("tools_used", []))
    # Señal del control de programa: SOLO cuando el turno trae contexto Y el
    # modelo llamó la tool no-op de rechazo explícito. Sin contexto ⇒ None.
    control_signal = (
        "declined"
        if request.control_context is not None and "mark_control_declined" in tools_used
        else None
    )

    return ChatResponse(
        thread_id=thread_id,
        answer=_extract_answer(result),
        agent=result.get("agent") or request.agent or request.agent_type_id or "base",
        tools_used=tools_used,
        model=result.get("provider") or model,
        execution_id=execution_id,
        suggestions=_normalize_suggestions(result.get("suggestions", [])),
        control_signal=control_signal,
    )


@router.post("/feedback", response_model=FeedbackResponse)
async def feedback(
    request: FeedbackRequest,
    session=Depends(get_db_session),
) -> FeedbackResponse:
    """Registra el feedback del usuario y alimenta la adaptive memory.

    Si vienen `agent_type_id` + `trigger` + `response`, el rating se convierte
    en una experiencia aprendida (patrón trigger → response con outcome
    success/error según el rating). El feedback siempre se persiste.
    """
    adaptive = AdaptiveMemoryService(session)
    await adaptive.save_feedback(
        thread_id=request.thread_id,
        rating=request.rating,
        comment=request.comment,
        user_id=request.user_id,
        execution_id=request.execution_id,
    )

    experience_saved = False
    outcome: str | None = None
    if request.agent_type_id and request.trigger and request.response:
        try:
            experience = await adaptive.save_experience(
                agent_type_id=request.agent_type_id,
                version_id=None,
                trigger=request.trigger,
                response=request.response,
                rating=request.rating,
            )
            experience_saved = True
            outcome = experience.outcome
        except Exception:
            logger.exception(
                "No se pudo guardar la experiencia para el agente %s",
                request.agent_type_id,
            )

    await session.commit()
    return FeedbackResponse(
        thread_id=request.thread_id,
        rating=request.rating,
        experience_saved=experience_saved,
        outcome=outcome,
    )


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    http_request: Request,
    session=Depends(get_db_session),
    base_graph=Depends(get_graph),
) -> StreamingResponse:
    """Streaming del agente vía Server-Sent Events (tokens + nodos en vivo)."""
    _check_rate_limit(http_request, request.user_id)
    thread_id = request.thread_id or str(uuid.uuid4())

    correlation_id = http_request.headers.get("x-correlation-id")
    if correlation_id:
        logger.info(
            "chat/stream: correlation_id=%s user=%s thread=%s agent=%s",
            correlation_id,
            request.user_id,
            thread_id,
            request.agent_type_id or request.agent or "base",
        )

    graph, recursion_limit, version_id, provider, model = await _resolve_graph(request, session)
    if graph is None:
        graph = base_graph

    tracker = ExecutionTracker(session)
    execution_id = await tracker.start(
        thread_id=thread_id,
        agent_type_id=request.agent_type_id or request.agent or "base",
        version_id=version_id,
        agent_instance_id=request.agent_instance_id,
        user_id=request.user_id,
        provider=provider,
        model=model,
        message=request.message,
    )

    started = time.perf_counter()
    flow_trace: list[dict[str, object]] = []

    async def event_generator():
        yield "event: start\ndata: {}\n\n"
        final_state: dict | None = None
        error: str | None = None
        # Trazabilidad de nodos para la visualización de flujos en vivo:
        # `stream_mode="tasks"` emite inicio (trae `triggers`) y fin (trae
        # `result`) de cada nodo; se traduce a eventos `flow` con step/ts.
        flow_step = 0
        running_tasks: dict[str, dict[str, Any]] = {}
        try:
            config = _build_config(thread_id, request, recursion_limit)
            agent_key = request.agent or request.agent_type_id or "base"

            async for mode, chunk in graph.astream(
                {"input": request.message, "agent": agent_key},
                config=config,
                stream_mode=["updates", "messages", "values", "tasks"],
            ):
                if mode == "values":
                    # Estado completo tras cada super-step; el último es el final.
                    if isinstance(chunk, dict):
                        final_state = chunk
                    continue
                if mode == "messages":
                    message_chunk, metadata = chunk
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
                        token_payload: dict[str, object] = {"type": "token", "content": text}
                        # Nodo que emite el token (el front marca el nodo activo).
                        node_name = (
                            metadata.get("langgraph_node")
                            if isinstance(metadata, dict)
                            else None
                        )
                        if node_name:
                            token_payload["node"] = node_name
                        payload = json.dumps(token_payload, ensure_ascii=False)
                        yield f"event: message\ndata: {payload}\n\n"
                elif mode == "updates":
                    for node_name in chunk:
                        payload = json.dumps({"type": "node", "node": node_name})
                        yield f"event: node\ndata: {payload}\n\n"
                elif mode == "tasks":
                    if not isinstance(chunk, dict):
                        continue
                    node_name = str(chunk.get("name") or "")
                    task_id = str(chunk.get("id") or "")
                    # Tasks internos de LangGraph (`__start__`, etc.) fuera.
                    if not node_name or not task_id or node_name.startswith("__"):
                        continue
                    now_ms = int(time.time() * 1000)
                    if "triggers" in chunk:
                        flow_step += 1
                        running_tasks[task_id] = {
                            "node": node_name,
                            "started_pc": time.perf_counter(),
                            "step": flow_step,
                            "ts": now_ms,
                        }
                        flow_trace.append(
                            {
                                "node": node_name,
                                "phase": "start",
                                "step": flow_step,
                                "ts": now_ms,
                            }
                        )
                        payload = json.dumps(
                            {
                                "type": "flow",
                                "node": node_name,
                                "phase": "start",
                                "step": flow_step,
                                "ts": now_ms,
                            },
                            ensure_ascii=False,
                        )
                        yield f"event: flow\ndata: {payload}\n\n"
                    else:
                        info = running_tasks.pop(task_id, None)
                        entry: dict[str, object] = {
                            "node": node_name,
                            "phase": "end",
                            "step": info["step"] if info else flow_step,
                            "ts": now_ms,
                        }
                        if info is not None:
                            entry["duration_ms"] = int(
                                (time.perf_counter() - float(info["started_pc"])) * 1000
                            )
                        flow_trace.append(entry)
                        payload = json.dumps({"type": "flow", **entry}, ensure_ascii=False)
                        yield f"event: flow\ndata: {payload}\n\n"
        except Exception as exc:
            logger.exception("Error en el stream del agente")
            error = str(exc)
            payload = json.dumps({"type": "error", "error": str(exc)}, ensure_ascii=False)
            yield f"event: error\ndata: {payload}\n\n"
        finally:
            latency_ms = int((time.perf_counter() - started) * 1000)
            if error is not None:
                await tracker.fail(execution_id, error=error, latency_ms=latency_ms)
            elif final_state is not None:
                await tracker.complete(
                    execution_id,
                    result_state=final_state,
                    model=model,
                    latency_ms=latency_ms,
                    flow_trace=flow_trace or None,
                )
            else:
                # Sin estado final (stream interrumpido): se registra como error
                # de forma conservadora.
                await tracker.fail(
                    execution_id,
                    error="Stream interrumpido sin respuesta final.",
                    latency_ms=latency_ms,
                )
            await session.commit()
        done_payload = {
            "thread_id": thread_id,
            "execution_id": execution_id,
            "suggestions": [
                s.model_dump()
                for s in _normalize_suggestions(
                    final_state.get("suggestions", []) if final_state else []
                )
            ],
        }
        # Señal del control de programa (UC-001 'Controles'): evento propio
        # DESPUÉS del stream de tokens y ANTES de `done`, solo cuando el turno
        # traía contexto Y el modelo llamó la tool de rechazo explícito. El
        # backend la consume y no la reenvía; la app móvil ignora líneas
        # desconocidas. Sin contexto ⇒ nunca se emite.
        if (
            request.control_context is not None
            and final_state is not None
            and "mark_control_declined" in (final_state.get("tools_used") or [])
        ):
            yield "event: control_signal\ndata: declined\n\n"
        yield f"event: done\ndata: {json.dumps(done_payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
