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
from app.api.schemas import ThreadMessageResponse, ThreadStateResponse
from app.api.security import require_internal_key

router = APIRouter(
    prefix="/threads",
    tags=["threads"],
    dependencies=[Depends(require_internal_key)],
)

# La app del paciente re-renderiza toda la conversación tras un re-login: el
# historial completo inflaría la respuesta sin aportar contexto útil, así que
# solo se devuelven los últimos 100 mensajes VISIBLES. El recorte aplica a la
# lista devuelta; `message_count` sigue reportando el total real del thread.
MAX_THREAD_MESSAGES = 100


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
    graph=Depends(get_graph),
) -> ThreadStateResponse:
    """Devuelve un resumen del historial persistido de un thread (solo del
    usuario autenticado que lo consulta), incluidos los mensajes visibles que
    la app del paciente necesita para reconstruir la conversación."""
    storage_thread_id = f"{user_id}::{thread_id}"
    snapshot = await graph.aget_state({"configurable": {"thread_id": storage_thread_id}})

    # Sin checkpoint el thread nunca existió (404). Un thread que existe pero
    # no tiene mensajes visibles es un historial vacío válido (200 con listas
    # vacías), no un error: la app lo trata como "conversación nueva".
    if snapshot is None or snapshot.created_at is None:
        raise HTTPException(status_code=404, detail="Thread no encontrado")

    messages = (snapshot.values or {}).get("messages", [])

    # Historial visible: se descartan los mensajes internos (tool/system) y
    # los que no aportan texto (p. ej. AIMessage que solo contiene tool_calls),
    # conservando el orden cronológico (más reciente al final).
    history: list[ThreadMessageResponse] = []
    for message in messages:
        role = _visible_role(message)
        if role is None:
            continue
        text = extract_message_text(getattr(message, "content", "") or "")
        if not text.strip():
            continue
        history.append(ThreadMessageResponse(role=role, text=text))

    last_message = None
    if messages:
        last = messages[-1]
        last_content = getattr(last, "content", "") or ""
        # content puede ser lista de bloques (Claude): extraer texto plano,
        # nunca str(lista) que mostraba "[{'text': ...}]" en el chat.
        last_message = extract_message_text(last_content)

    return ThreadStateResponse(
        thread_id=thread_id,
        message_count=len(messages),
        last_message=last_message,
        messages=history[-MAX_THREAD_MESSAGES:],
    )
