"""Dependencias FastAPI."""

from collections.abc import AsyncGenerator

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import AgentProfile
from app.agents.resolver import resolve_agent_profile
from app.core.errors import ConfigError
from app.db.engine import async_session
from app.graph.graph import build_graph
from app.graph.state import AgentState
from app.memory.checkpointer import get_checkpointer
from app.orchestration.router import IntentRouter

_graph = None
_router = IntentRouter()


async def _resolve_profile(state: AgentState) -> AgentProfile:
    """Resuelve el perfil de agente para el estado actual (routing por intención).

    Precedencia:
    1. Si el request trajo un `agent` explícito distinto de "base" (override del
       cliente/backend), se respeta ese perfil.
    2. Si no hay override, el router decide por el `input` del turno (keywords).
    3. La config (prompt/tools) se resuelve desde la BD (`ai.agent_runtime_configs`)
       con fallback al perfil de código. Nunca rompe el chat.
    """
    explicit = (state.get("agent") or "").strip()
    key = explicit if explicit and explicit != "base" else _router.route(state)

    # Sesión corta para leer la config del agente desde la BD (Opción A).
    async with async_session() as session:
        return await resolve_agent_profile(session, key)


async def get_graph():
    """Devuelve el grafo compilado (cacheado; los tests lo sobreescriben vía
    `app.dependency_overrides`).

    Es async porque el checkpointer de producción (`AsyncPostgresSaver`)
    necesita un event loop abierto para crear su pool de conexiones.
    """
    global _graph
    if _graph is not None:
        return _graph

    try:
        checkpointer = await get_checkpointer()
        _graph = build_graph(checkpointer=checkpointer, profile_resolver=_resolve_profile)
        return _graph
    except ConfigError as exc:
        # Ej: falta la API key del proveedor configurado
        raise HTTPException(
            status_code=503,
            detail=f"Servicio no configurado: {exc}",
        ) from exc


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Sesión SQLAlchemy asíncrona por request (schema `ai`)."""
    async with async_session() as session:
        yield session
