"""Consulta del estado de una conversación (gracias al checkpointer)."""

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_graph
from app.api.schemas import ThreadStateResponse

router = APIRouter(prefix="/threads", tags=["threads"])


@router.get("/{thread_id}/state", response_model=ThreadStateResponse)
async def thread_state(thread_id: str, graph=Depends(get_graph)) -> ThreadStateResponse:
    """Devuelve un resumen del historial persistido de un thread."""
    snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
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
