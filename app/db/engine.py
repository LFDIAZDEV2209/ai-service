"""Motor y sesiones SQLAlchemy asíncronas (schema `ai`).

Usa el mismo Postgres que el backend (variable `DATABASE_URL`). El driver es
psycopg3 (async), por eso la URL se normaliza a `postgresql+psycopg://`.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

DEFAULT_URL = "postgresql://app_user:CoppAddresdDev!2026@localhost:5432/coppaddresd"


def sqlalchemy_url() -> str:
    """Normaliza `DATABASE_URL` al dialecto de SQLAlchemy con psycopg3."""
    raw = get_settings().database_url or DEFAULT_URL
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
