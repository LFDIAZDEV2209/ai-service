"""Lectura del catálogo ERP compartido para resolver el alcance de RAG.

El backend conserva la propiedad y escritura de agents.knowledge_bases; este
módulo solo lee IDs activos. Alembic continúa administrando exclusivamente ai.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def resolve_knowledge_base_ids(
    session: AsyncSession, agent_type_id: str, configured_ids: list[str] | tuple[str, ...]
) -> list[str]:
    """Globales activas + KBs propias, respetando la selección explícita.

    Lista vacía significa todas las propias y globales; jamás significa quitar
    el filtro del vector store. Una lista explícita restringe las propias.
    """
    rows = (
        (
            await session.execute(
                text(
                    "SELECT id::text AS id, scope FROM agents.knowledge_bases "
                    "WHERE status = 'Activo' AND "
                    "(scope = 'Global' OR "
                    "(scope = 'Agent' AND agent_type_id::text = :agent_type_id))"
                ),
                {"agent_type_id": agent_type_id},
            )
        )
        .mappings()
        .all()
    )
    requested = set(configured_ids)
    return sorted(
        row["id"]
        for row in rows
        if row["scope"] == "Global" or not requested or row["id"] in requested
    )
