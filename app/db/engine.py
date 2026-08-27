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


def run_db_migrations() -> None:
    """Ejecuta las migraciones de Alembic si `auto_migrate` está habilitado.

    Alembic es idempotente (revisa `ai.alembic_version`).
    Además, `alembic/env.py` usa un Advisory Lock en PostgreSQL (`pg_advisory_lock`)
    para que si varios workers o instancias arrancan a la vez, no colisionen ni dupliquen ejecuciones.
    """
    settings = get_settings()
    if not settings.auto_migrate or not settings.database_url:
        return

    import logging
    from pathlib import Path
    from alembic import command
    from alembic.config import Config

    logger = logging.getLogger("app.db.migrations")
    ini_path = Path(__file__).resolve().parent.parent.parent / "alembic.ini"
    if not ini_path.exists():
        logger.warning("alembic.ini no encontrado en %s, se omiten migraciones automáticas", ini_path)
        return

    logger.info("Verificando migraciones automáticas de base de datos...")
    alembic_cfg = Config(str(ini_path))
    try:
        command.upgrade(alembic_cfg, "head")
        logger.info("Migraciones de base de datos verificadas/aplicadas exitosamente.")
    except Exception:
        logger.exception("Error al aplicar migraciones automáticas de Alembic")
        raise
