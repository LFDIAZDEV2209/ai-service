"""Motor y sesiones SQLAlchemy asíncronas (schema `ai`).

Usa el mismo Postgres que el backend (variable `DATABASE_URL`). El driver es
psycopg3 (async), por eso la URL se normaliza a `postgresql+psycopg://`.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.errors import ConfigError


def sqlalchemy_url() -> str:
    """Normaliza `DATABASE_URL` al dialecto de SQLAlchemy con psycopg3.

    La conexión se obtiene SIEMPRE de configuración externa (`DATABASE_URL`).
    Si falta, falla con un error claro: no existe URL por defecto (no hay
    credenciales hardcodeadas).
    """
    settings = get_settings()
    raw = settings.database_url
    if not raw:
        raise ConfigError(
            "Configuración incompleta: falta DATABASE_URL. "
            f"Entorno: {settings.environment}. "
            "Componente: app.db.engine (conexión SQLAlchemy async, schema ai). "
            "Configúrala en el archivo .env (variable DATABASE_URL)."
        )
    if raw.startswith("postgres://"):
        raw = raw.replace("postgres://", "postgresql://", 1)
    if raw.startswith("postgresql://") and "+psycopg" not in raw:
        raw = raw.replace("postgresql://", "postgresql+psycopg://", 1)
    return raw


# Motor asíncrono para el runtime (FastAPI/LangGraph).
engine = create_async_engine(sqlalchemy_url(), pool_pre_ping=True)

# Fábrica de sesiones asíncronas.
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def get_sync_engine():
    """Motor síncrono para herramientas de mantenimiento (Alembic)."""
    return create_engine(sqlalchemy_url(), pool_pre_ping=True)
