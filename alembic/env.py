"""Entorno de Alembic: migraciones del schema `ai` sobre el Postgres compartido.

La URL se toma de `DATABASE_URL` (misma base del backend). La tabla de control
`alembic_version` vive en el schema `ai` para no mezclarse con la de EF
(`public.__EFMigrationsHistory`).
"""

from __future__ import annotations

from sqlalchemy import engine_from_config, pool, text

from alembic import context
from app.db import models  # noqa: F401  (registra los modelos en Base.metadata)
from app.db.base import Base
from app.db.engine import sqlalchemy_url

SCHEMA = "ai"

config = context.config
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", sqlalchemy_url())

target_metadata = Base.metadata


def _ensure_schema(connection) -> None:
    """Crea el schema `ai` si no existe (incluida la primera migración)."""
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))


def include_object(obj, name: str, type_: str, reflected, compare_to) -> bool:
    """El autogenerate SOLO ve el schema `ai` (nunca toca tablas del backend)."""
    if type_ == "table":
        return getattr(obj, "schema", None) == SCHEMA
    return True


def run_migrations_offline() -> None:
    """Modo offline: genera SQL sin conectarse."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=SCHEMA,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Modo online: ejecuta las migraciones contra la base."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _ensure_schema(connection)
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=SCHEMA,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
