"""Consulta del estado de una conversación (gracias al checkpointer).

Canal interno: requiere `X-Internal-Key` (solo el backend .NET lo conoce).
El `user_id` real llega desde el backend (derivado del JWT) y se usa para
construir la clave interna del thread; un usuario jamás puede leer el estado
de un thread de otro usuario aunque adivine el `thread_id` (Pruebas A/B/C).
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.api.deps import get_graph
from app.api.message_text import extract_message_text
from app.api.schemas import HistoryResetResponse, ThreadMessageResponse, ThreadStateResponse
from app.api.security import require_internal_key
from app.history.service import history_service, storage_key
from app.history.service import metadata as reset_metadata
from app.memory.checkpointer import get_checkpointer
from app.vita.locking import ThreadBusyError, thread_guard
from app.voice.store import VoiceContextError

router = APIRouter(
    prefix="/threads",
    tags=["threads"],
    dependencies=[Depends(require_internal_key)],
)

# La app del paciente pagina hacia atrás: pide las últimas páginas de mensajes
# VISIBLES y va subiendo por el historial al hacer scroll. `limit` define el
# tamaño de página (tope MAX_THREAD_MESSAGES = 100) y `before` es el cursor de
# cuántos mensajes visibles saltear desde el más nuevo. `message_count` sigue
# reportando el total real del thread, no el de la página.
MAX_THREAD_MESSAGES = 100


@router.delete("/{thread_id}/state", response_model=HistoryResetResponse)
async def clear_thread_state(
    thread_id: str,
    user_id: str = Query(min_length=1, max_length=128),
    saver=Depends(get_checkpointer),
) -> HistoryResetResponse:
    """BACK resuelve propietario desde JWT; borrar no exige consentimiento de lectura."""
    try:
        async with thread_guard(storage_key(user_id, thread_id)):
            record = await history_service.clear_locked(user_id, thread_id, saver)
            return HistoryResetResponse(thread_id=thread_id, **reset_metadata(record))
    except ThreadBusyError as exc:
        raise HTTPException(status_code=409, detail="vita_thread_busy") from exc
    except VoiceContextError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="history_clear_unavailable") from exc


def _visible_role(message: BaseMessage) -> str | None:
    """Rol visible del mensaje para la app (None si es un mensaje interno).

    Solo los turnos del paciente (`HumanMessage` → "user") y del agente
    (`AIMessage` → "bot") se exponen; `ToolMessage`, `SystemMessage` y
    cualquier otro tipo quedan fuera porque la app no los renderiza.
    """
    if isinstance(message, HumanMessage):
        return "user"
    if isinstance(message, AIMessage):
        return "bot"
    return None


@router.get("/{thread_id}/state", response_model=ThreadStateResponse)
async def thread_state(
    thread_id: str,
    user_id: str = Query(..., min_length=1, description="ID del usuario propietario (backend/JWT)"),
    limit: int = Query(
        10,
        ge=1,
        le=MAX_THREAD_MESSAGES,
        description="Cantidad máxima de mensajes visibles por página.",
    ),
    before: int | None = Query(
        None,
        ge=0,
        description=(
            "Cursor de paginación: cuántos mensajes visibles saltear desde el "
            "más nuevo (null/ausente ⇒ página más reciente)."
        ),
    ),
    sync_voice: bool = Query(
        default=False, description="BACK: cliente opt-in y consentimiento chat+voz verificados"
    ),
    supports_vita: bool = Query(False, description="BACK: app Vita opt-in verificada"),
    graph=Depends(get_graph),
) -> ThreadStateResponse:
    """Devuelve una página del historial persistido de un thread (solo del
    usuario autenticado que lo consulta), incluidos los mensajes visibles que
    la app del paciente necesita para reconstruir la conversación.

    `limit` define el tamaño de la página y `before` el cursor que saltea
    mensajes visibles desde el más nuevo (null ⇒ página más reciente). La
    respuesta incluye `has_more` y `next_cursor` para pedir la página anterior.
    """
    try:
        key = storage_key(user_id, thread_id)
        # Solo la señal opt-in registra nuevos hilos. Una fila ya registrada
        # siempre cumple el corte, aunque un consumidor antiguo omita la señal.
        record = await history_service.get(user_id, thread_id)
        if record is not None or supports_vita or sync_voice:
            async with thread_guard(key):
                await history_service.ensure_locked(
                    user_id, thread_id, graph.checkpointer, opt_in=supports_vita or sync_voice
                )
    except ThreadBusyError as exc:
        raise HTTPException(status_code=409, detail="vita_thread_busy") from exc
    except VoiceContextError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="history_state_unavailable") from exc
    sync_status, bound = None, False
    if sync_voice:
        from app.voice.transcript import sync_closed_thread

        try:
            sync_status, bound = await sync_closed_thread(user_id, thread_id, graph)
        except VoiceContextError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    try:
        # Snapshot + revisión bajo un único lock: nunca devolver mensajes viejos
        # etiquetados con una revisión posterior al DELETE.
        async with thread_guard(key):
            record = await history_service.ensure_locked(user_id, thread_id, graph.checkpointer)
            history_metadata = reset_metadata(record)
            snapshot = await graph.aget_state({"configurable": {"thread_id": key}})
    except ThreadBusyError as exc:
        raise HTTPException(status_code=409, detail="vita_thread_busy") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="history_state_unavailable") from exc

    # Sin checkpoint el thread nunca existió (404). Un thread que existe pero
    # no tiene mensajes visibles es un historial vacío válido (200 con listas
    # vacías), no un error: la app lo trata como "conversación nueva".
    if snapshot is None or snapshot.created_at is None:
        if bound or (record is not None and record.cleared_at is not None):
            return ThreadStateResponse(
                thread_id=thread_id,
                message_count=0,
                voice_sync_status=sync_status,
                **history_metadata,
            )
        raise HTTPException(status_code=404, detail="Thread no encontrado")

    messages = (snapshot.values or {}).get("messages", [])

    # Historial visible: se descartan los mensajes internos (tool/system) y
    # los que no aportan texto (p. ej. AIMessage que solo contiene tool_calls),
    # conservando el orden cronológico (más reciente al final).
    visible: list[ThreadMessageResponse] = []
    for message in messages:
        role = _visible_role(message)
        if role is None:
            continue
        text = extract_message_text(getattr(message, "content", "") or "")
        if not text.strip():
            continue
        metadata = getattr(message, "additional_kwargs", {})
        visible.append(
            ThreadMessageResponse(
                role=role,
                text=text,
                message_id=message.id,
                channel=metadata.get("channel", "text"),
                status=metadata.get("status", "final"),
                revision=metadata.get("revision", 1),
                execution_id=metadata.get("execution_id"),
                source=metadata.get("source"),
                voice_session_id=metadata.get("voice_session_id"),
                provider_message_id=metadata.get("provider_message_id"),
                source_event_id=metadata.get("source_event_id"),
            )
        )

    last_message = None
    if messages:
        last = messages[-1]
        last_content = getattr(last, "content", "") or ""
        # content puede ser lista de bloques (Claude): extraer texto plano,
        # nunca str(lista) que mostraba "[{'text': ...}]" en el chat.
        last_message = extract_message_text(last_content)

    # Paginación hacia atrás: `before` cuenta cuántos mensajes visibles se
    # saltean desde el más nuevo y `limit` define el tamaño de la página. El
    # resultado va en orden cronológico y `next_cursor`, cuando hay más
    # historia, es el `before` que la app debe enviar en la próxima solicitud.
    total_visible = len(visible)
    offset = before or 0
    end = max(0, total_visible - offset)
    start = max(0, end - limit)
    page = visible[start:end]
    has_more = start > 0
    next_cursor = (total_visible - start) if has_more else None

    return ThreadStateResponse(
        thread_id=thread_id,
        **history_metadata,
        voice_sync_status=sync_status,
        message_count=len(messages),
        last_message=last_message,
        messages=page,
        has_more=has_more,
        next_cursor=next_cursor,
    )
