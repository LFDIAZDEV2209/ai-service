"""Consulta del estado de una conversación (gracias al checkpointer).

Canal interno: requiere `X-Internal-Key` (solo el backend .NET lo conoce).
El `user_id` real llega desde el backend (derivado del JWT) y se usa para
construir la clave interna del thread; un usuario jamás puede leer el estado
de un thread de otro usuario aunque adivine el `thread_id` (Pruebas A/B/C).
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_graph
from app.api.schemas import ThreadStateResponse
from app.api.security import require_internal_key

router = APIRouter(
    prefix="/threads",
    tags=["threads"],
    dependencies=[Depends(require_internal_key)],
)


@router.get("/{thread_id}/state", response_model=ThreadStateResponse)
async def thread_state(
    thread_id: str,
    user_id: str = Query(..., min_length=1, description="ID del usuario propietario (backend/JWT)"),
    graph=Depends(get_graph),
) -> ThreadStateResponse:
    """Devuelve un resumen del historial persistido de un thread (solo del
    usuario autenticado que lo consulta)."""
    storage_thread_id = f"{user_id}::{thread_id}"
    snapshot = await graph.aget_state({"configurable": {"thread_id": storage_thread_id}})
    messages = (snapshot.values or {}).get("messages", []) if snapshot else []

    if not messages:
        raise HTTPException(status_code=404, detail="Thread no encontrado")

    last = messages[-1]
    last_content = getattr(last, "content", "") or ""
    return ThreadStateResponse(
        thread_id=thread_id,
        message_count=len(messages),
        last_message=last_content if isinstance(last_content, str) else str(last_content),
    )
