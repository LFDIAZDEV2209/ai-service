"""Observabilidad: registro de ejecuciones del grafo en `ai.agent_executions`.

Cada turno de chat (sync o streaming) crea una fila de ejecución y la completa
al terminar: responde las 12 preguntas del plan de agentes (sección 13 del
prompt):

1. ¿Qué agente respondió?            → agent_type_id
2. ¿Qué versión estaba activa?       → version_id
3. ¿Qué modelo utilizó?              → provider/model (en input/output)
4. ¿Qué documentos recuperó?         → rag_sources (extraídas de la tool RAG)
5. ¿Qué memoria recuperó?            → memory_context/experience_context
6. ¿Qué tools ejecutó?               → tools_used
7. ¿Cuánto tardó?                    → latency_ms
8. ¿Cuántos tokens consumió?         → tokens_in/tokens_out (usage_metadata)
9. ¿Qué errores ocurrieron?          → status="error" + error
10. ¿Feedback del usuario?           → join ai.agent_feedback por thread_id
11. ¿Evaluación programática?        → join ai.agent_evaluations por execution_id
12. ¿Experiencias generadas?         → join ai.agent_experiences (agente)

El tracker es best-effort: si la persistencia falla, el chat sigue funcionando
(se registra el error y se continúa).
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import UTC, datetime

from langchain_core.messages import AIMessage, ToolMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentExecution
from app.llm.cost import SessionStats

logger = logging.getLogger(__name__)

# Bloque que la tool de retrieval inyecta al final de su resultado:
#   [FUENTES: [{"source": ..., "heading": ..., "score": ...}]]
# Estructura: `[FUENTES: ` + array JSON `[...]` + `]` del bloque = termina en `}]]`.
# El grupo captura el array completo (greedy) dejando la `]` final del bloque.
_FUENTES_RE = re.compile(r"\[FUENTES:\s*(\[.*\])\]", re.DOTALL)

_STATUS_RUNNING = "ejecutando"
_STATUS_OK = "completado"
_STATUS_ERROR = "error"


def extract_rag_sources(messages: list) -> list[dict]:
    """Extrae las fuentes RAG de los ToolMessages del turno.

    La tool `retrieve_knowledge` devuelve un bloque `[FUENTES: <json>]` al
    final de su resultado; aquí se parsea para registrarlo en la ejecución
    (el playground/monitoreo las muestra).
    """
    sources: list[dict] = []
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        content = getattr(msg, "content", "")
        if not isinstance(content, str):
            continue
        for match in _FUENTES_RE.finditer(content):
            try:
                parsed = json.loads(match.group(1))
                if isinstance(parsed, list):
                    sources.extend(item for item in parsed if isinstance(item, dict))
            except (json.JSONDecodeError, TypeError):
                continue
    return sources


def _usage_stats(messages: list, model: str) -> tuple[int, int]:
    """Suma tokens de uso reportados por los AIMessages (usage_metadata)."""
    stats = SessionStats(model=model)
    for msg in messages:
        if isinstance(msg, AIMessage):
            stats.add_message(msg)
    return stats.input_tokens, stats.output_tokens


def _message_text(content) -> str:
    """Extrae el texto de un AIMessage.content (str o lista de bloques)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(parts)
    return ""


class ExecutionTracker:
    """Registra una ejecución del grafo en `ai.agent_executions` (best-effort)."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def start(
        self,
        *,
        thread_id: str,
        agent_type_id: str,
        version_id: str | None,
        agent_instance_id: str | None,
        user_id: str | None,
        provider: str | None,
        model: str | None,
        message: str,
        memory_context: str = "",
        experience_context: str = "",
    ) -> str:
        """Crea la fila de ejecución en estado `ejecutando`; devuelve su id."""
        execution_id = str(uuid.uuid4())
        execution = AgentExecution(
            id=execution_id,
            thread_id=thread_id,
            agent_type_id=agent_type_id or "base",
            version_id=version_id,
            agent_instance_id=agent_instance_id,
            user_id=user_id,
            status=_STATUS_RUNNING,
            input={
                "message": message,
                "memory_context": memory_context or None,
                "experience_context": experience_context or None,
                "provider": provider,
                "model": model,
            },
            created_at=datetime.now(UTC),
        )
        self._session.add(execution)
        return execution_id

    async def complete(
        self,
        execution_id: str,
        *,
        result_state: dict,
        model: str | None,
        latency_ms: int,
        flow_trace: list[dict] | None = None,
    ) -> None:
        """Completa la ejecución con métricas del estado final del grafo.

        `flow_trace` (opcional): traza de nodos del stream (start/end con
        duración) para reproducir el flujo en la UI de visualización.
        """
        messages = list(result_state.get("messages", []))
        tokens_in, tokens_out = _usage_stats(messages, model or "unknown")
        answer = ""
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                text = _message_text(getattr(msg, "content", ""))
                if text:
                    answer = text
                    break

        execution = await self._session.get(AgentExecution, execution_id)
        if execution is None:
            return
        execution.status = _STATUS_OK
        output: dict = {
            "answer": answer or None,
            "tools_used": result_state.get("tools_used", []),
            "rag_sources": extract_rag_sources(messages),
            "suggestions": result_state.get("suggestions", []),
            "provider": result_state.get("provider") or model,
            "model": model or result_state.get("provider"),
        }
        if flow_trace:
            output["flow_trace"] = flow_trace
        execution.output = output
        execution.tokens_in = tokens_in
        execution.tokens_out = tokens_out
        execution.latency_ms = latency_ms
        execution.ended_at = datetime.now(UTC)

    async def fail(
        self,
        execution_id: str,
        *,
        error: str,
        latency_ms: int,
    ) -> None:
        """Marca la ejecución como fallida con el mensaje de error."""
        execution = await self._session.get(AgentExecution, execution_id)
        if execution is None:
            return
        execution.status = _STATUS_ERROR
        execution.error = error[:2000]
        execution.latency_ms = latency_ms
        execution.ended_at = datetime.now(UTC)


def _now_ms() -> int:
    return int(time.monotonic() * 1000)
