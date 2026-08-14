"""Dependencias FastAPI."""

from collections.abc import AsyncGenerator

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConfigError
from app.db.engine import async_session
from app.graph.graph import build_graph
from app.memory.checkpointer import get_checkpointer

_graph = None


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
        _graph = build_graph(checkpointer=checkpointer)
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
