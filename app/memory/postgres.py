"""Persistencia de memoria de largo plazo del usuario en `ai.agent_memories`.

Aislamiento estricto: TODA operación de lectura/escritura recibe el
`user_id` del usuario autenticado (y opcionalmente `agent_type_id` /
`agent_instance_id`). Nunca existe un método que liste memorias sin filtro de
usuario; la inyección SQL está prohibida (siempre queries parametrizadas
vía SQLAlchemy). Esto garantiza que un agente nunca recupere la memoria de
otro paciente.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentMemory

logger = logging.getLogger(__name__)

# Límites de defensa en profundidad (además de la config del agente).
_MAX_MEMORIES_PER_USER = 500
_MAX_CONTEXT_MEMORIES = 15


class MemoryStoreError(Exception):
    """Error de persistencia de memoria."""


class UserMemoryStore:
    """Acceso a `ai.agent_memories` aislado por usuario.

    Args:
        session: sesión SQLAlchemy async (una por operación, corta).
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_memories(
        self,
        *,
        user_id: str,
        agent_type_id: str,
        agent_instance_id: str | None = None,
        categories: list[str] | None = None,
        limit: int = _MAX_CONTEXT_MEMORIES,
    ) -> list[AgentMemory]:
        """Memorias del usuario, más relevantes primero.

        Orden: importancia descendente, luego último acceso. `categories`
        restringe por categoría (p. ej. solo "resumen" para el contexto corto).
        """
        query = (
            select(AgentMemory)
            .where(
                AgentMemory.user_id == user_id,
                AgentMemory.agent_type_id == agent_type_id,
            )
            .order_by(
                AgentMemory.importance.desc(),
                AgentMemory.last_accessed_at.desc().nullslast(),
                AgentMemory.updated_at.desc().nullslast(),
                AgentMemory.created_at.desc(),
            )
            .limit(limit)
        )
        if agent_instance_id is not None:
            query = query.where(AgentMemory.agent_instance_id == agent_instance_id)
        if categories:
            query = query.where(AgentMemory.category.in_(categories))

        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def save_memory(
        self,
        *,
        user_id: str,
        agent_type_id: str,
        agent_instance_id: str | None,
        category: str,
        content: str,
        importance: float = 0.5,
        source: str = "chat",
        dedupe_key: str | None = None,
    ) -> AgentMemory:
        """Guarda una memoria, deduplicando por `dedupe_key` (p. ej. el
        contenido normalizado): si existe una memoria con la misma clave para
        el mismo usuario/agente, actualiza contenido/importancia en vez de
        duplicar (rollover de "resumen" incluido)."""
        existing = None
        if dedupe_key is not None:
            existing = (
                await self._session.execute(
                    select(AgentMemory).where(
                        AgentMemory.user_id == user_id,
                        AgentMemory.agent_type_id == agent_type_id,
                        AgentMemory.content == dedupe_key,
                    )
                )
            ).scalar_one_or_none()

        if existing is not None:
            existing.content = content
            existing.importance = importance
            existing.updated_at = datetime.now(UTC)
            return existing

        memory = AgentMemory(
            agent_instance_id=agent_instance_id,
            user_id=user_id,
            agent_type_id=agent_type_id,
            category=category,
            content=content,
            importance=importance,
            source=source,
        )
        self._session.add(memory)

        # Tope de memorias por usuario (política de higiene: las más antiguas
        # de la categoría "resumen" se descartan primero).
        total = (
            await self._session.execute(
                select(func.count(AgentMemory.id)).where(
                    AgentMemory.user_id == user_id,
                    AgentMemory.agent_type_id == agent_type_id,
                )
            )
        ).scalar_one()
        if total > _MAX_MEMORIES_PER_USER:
            oldest_resumen = (
                await self._session.execute(
                    select(AgentMemory.id)
                    .where(
                        AgentMemory.user_id == user_id,
                        AgentMemory.agent_type_id == agent_type_id,
                        AgentMemory.category == "resumen",
                    )
                    .order_by(AgentMemory.created_at.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if oldest_resumen is not None:
                await self._session.execute(
                    delete(AgentMemory).where(AgentMemory.id == oldest_resumen)
                )
                logger.info("Memoria antigua descartada (límite %d)", _MAX_MEMORIES_PER_USER)

        return memory

    async def touch_memory(self, memory_id: str) -> None:
        """Actualiza `last_accessed_at` (se usa al inyectar al contexto)."""
        memory = await self._session.get(AgentMemory, memory_id)
        if memory is not None:
            memory.last_accessed_at = datetime.now(UTC)

    async def delete_memories(
        self,
        *,
        user_id: str,
        agent_type_id: str,
        agent_instance_id: str | None = None,
    ) -> int:
        """Elimina las memorias del usuario para este agente (nunca las de otros)."""
        query = delete(AgentMemory).where(
            AgentMemory.user_id == user_id,
            AgentMemory.agent_type_id == agent_type_id,
        )
        if agent_instance_id is not None:
            query = query.where(AgentMemory.agent_instance_id == agent_instance_id)
        result = await self._session.execute(query)
        return result.rowcount or 0
