"""Endpoints internos protegidos por X-Internal-Key (backend → AI Service)."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runtime_registry import registry as runtime_registry
from app.api.deps import get_db_session
from app.core.config import get_settings
from app.db.models import AgentRuntimeConfig

router = APIRouter(prefix="/internal", tags=["internal"])


class AgentConfigSyncRequest(BaseModel):
    """Payload de sincronización de la versión activa de un agente."""

    agent_type_id: str = Field(min_length=1)
    version_id: str = Field(min_length=1)
    version_number: int = Field(ge=1)
    name: str = Field(min_length=1)
    description: str | None = None
    specialty: str | None = None
    icon_key: str | None = None
    config: dict[str, Any]


class AgentConfigSyncResponse(BaseModel):
    agent_type_id: str
    version_id: str
    synced: bool = True


def _require_internal_key(x_internal_key: str | None) -> None:
    """Valida el header X-Internal-Key contra la configuración compartida."""
    expected = get_settings().internal_api_key
    if not expected:
        raise HTTPException(status_code=503, detail="internal_api_key no configurada")
    if x_internal_key != expected:
        raise HTTPException(status_code=401, detail="Clave interna inválida")


@router.post("/agents/sync-config", response_model=AgentConfigSyncResponse)
async def sync_agent_config(
    payload: AgentConfigSyncRequest,
    x_internal_key: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db_session),
) -> AgentConfigSyncResponse:
    """Upsert de la configuración de runtime de un tipo de agente.

    El backend es la fuente de verdad del catálogo; este endpoint cachea en
    `ai.agent_runtime_configs` la versión activa para que el runtime compile
    los grafos sin round-trips al backend en cada chat.
    """
    _require_internal_key(x_internal_key)

    now = datetime.now(UTC)
    existing = (
        await session.execute(
            select(AgentRuntimeConfig).where(
                AgentRuntimeConfig.agent_type_id == payload.agent_type_id
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        session.add(
            AgentRuntimeConfig(
                agent_type_id=payload.agent_type_id,
                version_id=payload.version_id,
                is_active=True,
                config=payload.config,
                synced_at=now,
            )
        )
    else:
        await session.execute(
            update(AgentRuntimeConfig)
            .where(AgentRuntimeConfig.id == existing.id)
            .values(
                version_id=payload.version_id,
                is_active=True,
                config=payload.config,
                synced_at=now,
                updated_at=now,
            )
        )

    await session.commit()

    # Descarta el grafo cacheado: la próxima ejecución recompila con la
    # nueva versión activa.
    runtime_registry.invalidate(payload.agent_type_id)

    return AgentConfigSyncResponse(
        agent_type_id=payload.agent_type_id,
        version_id=payload.version_id,
    )
