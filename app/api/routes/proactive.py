"""Endpoint interno de mensajes proactivos (inyección en el thread SIN LLM).

Permite que el "chat inicie la conversación" con mensajes predeterminados
(push notifications): el backend .NET inyecta un `AIMessage` en el historial
del thread de un usuario sin invocar al modelo — costo cero.

El historial real vive en el checkpointer de LangGraph (`AsyncPostgresSaver`,
schema `ai`), NO en `ai.messages` (esa tabla es solo la vista de negocio).
Por eso la inyección usa `graph.aupdate_state`, que aplica el reducer
`add_messages` del estado (acumula y deduplica por id) y persiste vía el
checkpointer. Nunca se llama a `graph.ainvoke` (eso invocaría al LLM).

Canal interno: requiere `X-Internal-Key` (solo el backend .NET lo conoce).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field

from app.api.deps import get_graph
from app.api.routes.chat import _storage_thread_id
from app.api.security import require_internal_key

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal/agents",
    tags=["internal"],
    dependencies=[Depends(require_internal_key)],
)


class ProactiveMessageRequest(BaseModel):
    """Payload de inyección de un mensaje proactivo del bot."""

    user_id: str = Field(min_length=1)
    agent_type_id: str = "base"
    message: str = Field(min_length=1)
    # Si se omite, se usa un thread estable del usuario (p. ej. `proactive-{user_id}`).
    thread_id: str | None = None


class ProactiveMessageResponse(BaseModel):
    thread_id: str
    message_id: str | None = None


@router.post("/proactive-message", response_model=ProactiveMessageResponse)
async def proactive_message(
    payload: ProactiveMessageRequest,
    graph=Depends(get_graph),
) -> ProactiveMessageResponse:
    """Inyecta un mensaje del bot en el historial de un thread SIN invocar al LLM.

    - Si `thread_id` viene, se usa tal cual; si no, se usa un thread estable
      (`proactive-{user_id}`) para que las notificaciones de un usuario
      compartan un único historial.
    - El config aísla el thread por `user_id` (misma clave de `_storage_thread_id`
      que en `/chat`), así un usuario jamás comparte historial con otro.
    - Solo se usa `aupdate_state` + el reducer `add_messages`; el checkpointer
      crea el thread si no existe y persiste el nuevo mensaje. Costo LLM: cero.
    """
    thread_id = payload.thread_id or f"proactive-{payload.user_id}"
    storage_thread_id = _storage_thread_id(payload.user_id, thread_id)
    config = {"configurable": {"thread_id": storage_thread_id}}

    # El AIMessage recibe un id automático (uuid) al construirse; `add_messages`
    # lo agrega tal cual al estado (no hay duplicado previo en un thread nuevo).
    # `as_node="agent"` es obligatorio cuando el thread ya tiene checkpoints:
    # sin él, LangGraph no puede deducir qué nodo actualizó el estado y lanza
    # `InvalidUpdateError: Ambiguous update, specify as_node`.
    message = AIMessage(content=payload.message)

    try:
        await graph.aupdate_state(config, {"messages": [message]}, as_node="agent")
    except Exception as exc:
        logger.exception(
            "Error inyectando mensaje proactivo user=%s thread=%s",
            payload.user_id,
            thread_id,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Error inyectando mensaje proactivo: {exc}",
        ) from exc

    # Devuelve el id real persistido (re-leyendo el estado) para trazabilidad.
    message_id = message.id
    try:
        snapshot = await graph.aget_state(config)
        messages = (snapshot.values or {}).get("messages", []) if snapshot else []
        if messages:
            message_id = getattr(messages[-1], "id", None) or message_id
    except Exception:
        logger.warning("No se pudo confirmar el id del mensaje proactivo", exc_info=True)

    return ProactiveMessageResponse(thread_id=thread_id, message_id=message_id)
