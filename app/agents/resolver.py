"""Resolución de perfiles de agente desde la base de datos (Opción A).

Fuente de verdad: `ai.agent_runtime_configs`. Cada fila activa guarda el JSON
`config` que interpreta `AgentRuntimeConfig` (prompt, tools, provider, RAG,
memoria). El router decide la CLAVE por intención (keywords); este módulo
resuelve la CONFIG de esa clave desde la BD, con fallback al código.

Ver `docs/agents-config.md` para el diseño y las decisiones.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.knowledge_scope import resolve_knowledge_base_ids
from app.agents.registry import AgentProfile, get_agent_profile
from app.agents.runtime_config import AgentRuntimeConfig
from app.db.models import AgentRuntimeConfig as AgentRuntimeConfigRow
from app.tools.patient_context import get_patient_data
from app.tools.retrieval import make_retrieve_tool

logger = logging.getLogger(__name__)


async def _resolve_agent_type_guid(session: AsyncSession, key: str) -> str | None:
    """Slug del router → GUID del AgentType del ERP (None si ya es UUID/no existe).

    Las filas sincronizadas por el backend y las KBs `Agent` viven bajo el
    GUID del AgentType; el perfil del chat general llega con el slug.
    """
    try:
        uuid.UUID(str(key))
        return None  # es GUID ya; la clave sirve tal cual
    except (ValueError, AttributeError, TypeError):
        pass
    if not key or not key.strip():
        return None
    try:
        lookup = (
            await session.execute(
                text(
                    "SELECT id::text FROM agents.agent_types "
                    "WHERE slug = :slug ORDER BY created_at LIMIT 1"
                ),
                {"slug": key.strip().lower()},
            )
        ).first()
        return str(lookup[0]) if lookup else None
    except Exception:
        # Sin catálogo accesible (sessions fake en tests) → la clave sirve tal cual.
        logger.debug("No se pudo resolver slug→GUID para %s", key, exc_info=True)
        return None


def _with_patient_tool(profile: AgentProfile) -> AgentProfile:
    """Agrega contexto clínico solo a los perfiles que pueden necesitarlo."""
    if profile.key not in {"psychology", "medical", "nutrition"}:
        return profile
    return replace(profile, extra_tools=(get_patient_data, *profile.extra_tools))


async def resolve_agent_profile(
    session: AsyncSession,
    key: str,
) -> AgentProfile:
    """Resuelve el perfil de un agente, priorizando la BD sobre el código.

    Args:
        session: sesión SQLAlchemy (schema `ai`).
        key: clave del agente (`nutrition`, `medical`, `psychology`, `base`).

    Returns:
        `AgentProfile` construido desde `ai.agent_runtime_configs[key]` si
        existe una fila activa; si no, el perfil de `app/agents/registry.py`
        (fallback de código, nunca rompe el chat).
    """
    agent_guid = await _resolve_agent_type_guid(session, key)
    # Los re-seeds del backend reescriben filas por SLUG con synced_at fresco
    # (AgentCatalogSeeder en cada arranque), mientras las activaciones del ERP
    # viven bajo el GUID. La fila del GUID tiene precedencia; el slug es solo
    # legado (otherwize un seed posterior "gana" y apaga RAG/funciones del
    # perfil general). Anchura única por turno: nunca MultipleResultsFound
    # (incidente 17-16 UTC del 08-11).
    row = None
    if agent_guid:
        row = (
            await session.execute(
                select(AgentRuntimeConfigRow).where(
                    AgentRuntimeConfigRow.agent_type_id == agent_guid,
                    AgentRuntimeConfigRow.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
    if row is None:
        row = (
            await session.execute(
                select(AgentRuntimeConfigRow).where(
                    AgentRuntimeConfigRow.agent_type_id == key,
                    AgentRuntimeConfigRow.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()

    if row is None:
        logger.debug("Agente %s sin config en BD; usando perfil de código", key)
        return _with_patient_tool(get_agent_profile(key))

    try:
        runtime = AgentRuntimeConfig.model_validate(row.config)
    except Exception:
        # Config inválida en BD → no tumbar el chat, degradar al perfil base.
        logger.exception("Config inválida para agente %s; fallback a código", key)
        return _with_patient_tool(get_agent_profile(key))

    extra_tools = ()
    retrieval = runtime.retrieval_config
    if retrieval.enabled:
        # El SCOPE de KBs se resuelve SIEMPRE contra el GUID del AgentType:
        # las KBs `Agent` se enlazan por GUID; el slug del router solo es la
        # clave de perfil del chat general.
        kb_keys = str(agent_guid or key)
        kb_ids = await resolve_knowledge_base_ids(session, kb_keys, retrieval.knowledge_base_ids)
        # Sin alcance explícito no se enlaza retrieval: nunca consultar KBs ajenas.
        if kb_ids:
            extra_tools = (make_retrieve_tool(kb_ids, retrieval.top_k),)

    return _with_patient_tool(
        AgentProfile(
            key=key,
            name=runtime.system_prompt.splitlines()[0] if runtime.system_prompt else key,
            description="Configurado desde la base de datos",
            system_prompt=runtime.effective_system_prompt(),
            provider=runtime.provider,
            model=runtime.model,
            tools=tuple(runtime.tools),
            extra_tools=extra_tools,
        )
    )
