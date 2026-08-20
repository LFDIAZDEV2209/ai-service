"""Consulta de agentes configurados.

Endpoints de LECTURA para el futuro front de administración: listan los
perfiles de agente y su configuración efectiva. La configuración activa se
resuelve desde la BD (`ai.agent_runtime_configs`); si no existe, se muestra el
perfil de código (`app/agents/registry.py`) como referencia.

Ver `docs/agents-config.md`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import AGENTS
from app.api.deps import get_db_session
from app.api.security import require_internal_key
from app.db.models import AgentRuntimeConfig as AgentRuntimeConfigRow

# Canal interno: los agentes son configuración administrativa del backend
# (X-Internal-Key). No se exponen system prompts ni instrucciones internas.
router = APIRouter(
    prefix="/agents",
    tags=["agents"],
    dependencies=[Depends(require_internal_key)],
)


class AgentConfigOut(BaseModel):
    key: str
    name: str
    description: str
    provider: str | None = None
    model: str | None = None
    tools: list[str] = []
    from_code: bool = True


async def _load_configs(session: AsyncSession) -> dict[str, AgentRuntimeConfigRow]:
    rows = (await session.execute(select(AgentRuntimeConfigRow))).scalars().all()
    # La fila activa por agente (si hubiera varias, la activa).
    active: dict[str, AgentRuntimeConfigRow] = {}
    for row in rows:
        if row.is_active:
            active[row.agent_type_id] = row
    return active


@router.get("", response_model=list[AgentConfigOut])
async def list_agents(
    session: AsyncSession = Depends(get_db_session),
) -> list[AgentConfigOut]:
    """Lista los agentes disponibles con su configuración efectiva."""
    configs = await _load_configs(session)
    result: list[AgentConfigOut] = []

    for key, profile in AGENTS.items():
        row = configs.get(key)
        if row is not None:
            cfg = row.config
            result.append(
                AgentConfigOut(
                    key=key,
                    name=profile.name,
                    description=profile.description,
                    provider=cfg.get("provider"),
                    model=cfg.get("model"),
                    tools=list(cfg.get("tools", [])),
                    from_code=False,
                )
            )
        else:
            result.append(
                AgentConfigOut(
                    key=key,
                    name=profile.name,
                    description=profile.description,
                    provider=profile.provider,
                    model=profile.model,
                    tools=list(profile.tools),
                    from_code=True,
                )
            )

    return result


@router.get("/{key}", response_model=AgentConfigOut)
async def get_agent(
    key: str,
    session: AsyncSession = Depends(get_db_session),
) -> AgentConfigOut:
    """Devuelve la configuración efectiva de un agente por su clave."""
    profile = AGENTS.get(key)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Agente desconocido: {key!r}")

    configs = await _load_configs(session)
    row = configs.get(key)
    if row is not None:
        cfg = row.config
        return AgentConfigOut(
            key=key,
            name=profile.name,
            description=profile.description,
            provider=cfg.get("provider"),
            model=cfg.get("model"),
            tools=list(cfg.get("tools", [])),
            from_code=False,
        )

    return AgentConfigOut(
        key=key,
        name=profile.name,
        description=profile.description,
        provider=profile.provider,
        model=profile.model,
        tools=list(profile.tools),
        from_code=True,
    )
