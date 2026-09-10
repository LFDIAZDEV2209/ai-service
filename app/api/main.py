"""Aplicación FastAPI del servicio de IA."""

import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    admin,
    agents,
    chat,
    health,
    ingest,
    internal,
    lab_exam,
    proactive,
    threads,
)
from app.core.config import get_settings
from app.core.logging import setup_logging

# Windows: psycopg async solo funciona con SelectorEventLoop. uvicorn 0.36+
# fuerza ProactorEventLoop al arrancar, pero cualquier `asyncio.run` interno
# del proceso usa la policy — fijarla en import evita fallos de psycopg en
# helpers que crean loops propios.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    settings = get_settings()
    if settings.auto_migrate and settings.database_url:
        from app.db.engine import run_db_migrations

        await asyncio.to_thread(run_db_migrations)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        description="Agente IA de CoppAddresd — LangGraph + FastAPI (multi-proveedor).",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS: allowlist explícita desde configuración (settings.cors_origins).
    # No hay clientes de navegador del AI Service: el backend .NET lo consume
    # server-to-server. En producción el servicio debe escuchar solo en red
    # interna (docs/observability.md) y CORS_ORIGINS define la allowlist.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    prefix = settings.api_prefix
    app.include_router(health.router, prefix=prefix)
    app.include_router(chat.router, prefix=prefix)
    app.include_router(lab_exam.router, prefix=prefix)
    app.include_router(threads.router, prefix=prefix)
    app.include_router(agents.router, prefix=prefix)
    app.include_router(ingest.router, prefix=prefix)
    app.include_router(admin.router, prefix=prefix)

    # Endpoints internos (backend → AI Service); van fuera del prefix público.
    app.include_router(internal.router)
    app.include_router(proactive.router)
    app.include_router(lab_exam.router)

    return app
