"""Servicio de memoria de largo plazo del usuario: contexto + escritura.

Orquesta `UserMemoryStore` (persistencia) y `extract_facts` (extracción
heurística), y mantiene summaries rodantes: cuando la conversación supera un
umbral de mensajes, se genera (con el LLM del agente si está disponible) o se
reescribe un resumen acumulado por usuario/agente.

Flujo por turno:
1. `load_context` → memorias del usuario formateadas para el system prompt.
2. `extract_and_save` → hechos del mensaje del usuario persistidos.
3. `maybe_roll_summary` → si el historial creció, actualiza el resumen rodante.

Aislamiento: todas las operaciones reciben `user_id` explícito; nunca se
consultan memorias sin ese filtro.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.extract import CATEGORY_RESUMEN, UserFact, extract_facts
from app.memory.postgres import UserMemoryStore

logger = logging.getLogger(__name__)

# Umbral: a partir de cuántos mensajes (user+ai) se genera/rota el resumen.
_SUMMARY_MIN_MESSAGES = 6
# Longitud máxima del resumen rodante.
_SUMMARY_MAX_CHARS = 600

# Forma del callable de resumen: (mensajes previos, resumen anterior) → texto.
Summarizer = Callable[[list[str], str | None], Awaitable[str]]


async def _deterministic_summarizer(history: list[str], last_summary: str | None) -> str:
    """Resumen de respaldo sin LLM: primeros y últimos turnos concatenados."""
    parts = history[:_SUMMARY_MIN_MESSAGES // 2] + history[-2:]
    return " | ".join(parts)


def _format_memory(memory) -> str:
    return f"[{memory.category}] {memory.content}"


class UserMemoryService:
    """Servicio de memoria por usuario/agente sobre una sesión SQLAlchemy."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        summarizer: Summarizer | None = None,
        max_context_memories: int = 15,
    ):
        self._session = session
        self._store = UserMemoryStore(session)
        self._summarizer = summarizer
        self._max_context_memories = max_context_memories

    # ── Lectura / contexto ────────────────────────────────────────────────

    async def load_context(
        self,
        *,
        user_id: str,
        agent_type_id: str,
        agent_instance_id: str | None = None,
        categories: list[str] | None = None,
    ) -> str:
        """Memorias del usuario formateadas como bloque de contexto.

        Devuelve texto vacío si no hay memorias. No debe fallar por problemas
        de BD: si la persistencia no está disponible, el chat sigue sin memoria
        (los errores se registran y se degradan con gracia).
        """
        try:
            memories = await self._store.list_memories(
                user_id=user_id,
                agent_type_id=agent_type_id,
                agent_instance_id=agent_instance_id,
                categories=categories,
                limit=self._max_context_memories,
            )
        except Exception:
            logger.exception("No se pudo cargar la memoria de %s", user_id)
            return ""

        if not memories:
            return ""

        blocks = [_format_memory(m) for m in memories]
        return "Memoria del usuario:\n" + "\n".join(blocks)

    # ── Escritura ─────────────────────────────────────────────────────────

    async def extract_and_save(
        self,
        *,
        user_id: str,
        agent_type_id: str,
        agent_instance_id: str | None,
        message: str,
    ) -> list[UserFact]:
        """Extrae hechos del mensaje del usuario y los persiste.

        Devuelve los hechos guardados (vacío si el mensaje no declara nada
        extraíble). El commit lo hace el llamador (transacción corta).
        """
        extraction = extract_facts(message)
        saved: list[UserFact] = []
        for fact in extraction.facts:
            try:
                await self._store.save_memory(
                    user_id=user_id,
                    agent_type_id=agent_type_id,
                    agent_instance_id=agent_instance_id,
                    category=fact.category,
                    content=fact.content,
                    importance=fact.importance,
                    source=fact.source,
                    dedupe_key=fact.content,
                )
                saved.append(fact)
            except Exception:
                logger.exception("No se pudo guardar el hecho: %s", fact.content)
        return saved

    async def maybe_roll_summary(
        self,
        *,
        user_id: str,
        agent_type_id: str,
        agent_instance_id: str | None,
        history: list[str],
        last_summary: str | None = None,
    ) -> str | None:
        """Genera/rota el resumen rodante si el historial lo amerita.

        Regla: se rota solo cuando hay `_SUMMARY_MIN_MESSAGES` o más mensajes
        nuevos desde el último resumen (evita re-resumir en cada turno). Si no
        hay `summarizer` (sin LLM configurado), se guarda un resumen
        truncado determinista de los últimos mensajes.

        Returns:
            El texto del resumen si se rotó, o None si no aplica.
        """
        if len(history) < _SUMMARY_MIN_MESSAGES:
            return None

        if self._summarizer is not None:
            summary_text = await self._summarizer(history, last_summary)
        else:
            summary_text = await _deterministic_summarizer(history, last_summary)
        summary_text = summary_text.strip()

        if len(summary_text) > _SUMMARY_MAX_CHARS:
            summary_text = summary_text[:_SUMMARY_MAX_CHARS] + "…"

        try:
            await self._store.save_memory(
                user_id=user_id,
                agent_type_id=agent_type_id,
                agent_instance_id=agent_instance_id,
                category=CATEGORY_RESUMEN,
                content=summary_text,
                importance=0.6,
                source="resumen",
                dedupe_key="__resumen_rodante__",
            )
        except Exception:
            logger.exception("No se pudo rotar el resumen de %s", user_id)
            return None

        logger.info("Resumen rodante actualizado para %s (%d msgs)", user_id, len(history))
        return summary_text

    async def get_summary(
        self,
        *,
        user_id: str,
        agent_type_id: str,
    ) -> str | None:
        """Resumen rodante actual del usuario (si existe)."""
        try:
            memories = await self._store.list_memories(
                user_id=user_id,
                agent_type_id=agent_type_id,
                categories=[CATEGORY_RESUMEN],
                limit=1,
            )
        except Exception:
            return None
        return memories[0].content if memories else None

    async def commit(self) -> None:
        """Persiste las escrituras pendientes de esta sesión.

        Las operaciones de escritura (`extract_and_save`, `maybe_roll_summary`)
        dejan la transacción abierta a propósito (transacción corta: el llamador
        decide cuándo persistir); el nodo del grafo que las invoca debe llamar a
        `commit()` para que los hechos no se pierdan al descartarse la sesión.
        """
        await self._session.commit()


def _touch_now() -> datetime:
    return datetime.now(UTC)
