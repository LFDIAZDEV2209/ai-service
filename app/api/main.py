"""Aplicación FastAPI del servicio de IA."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import chat, health, ingest, internal, threads
from app.core.config import get_settings
from app.core.logging import setup_logging


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        description="Agente IA de CoppAddresd — LangGraph + FastAPI (multi-proveedor).",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS abierto para desarrollo (ajustar orígenes en producción)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    prefix = settings.api_prefix
    app.include_router(health.router, prefix=prefix)
    app.include_router(chat.router, prefix=prefix)
    app.include_router(threads.router, prefix=prefix)
    app.include_router(ingest.router, prefix=prefix)

    # Endpoints internos (backend → AI Service); van fuera del prefix público.
    app.include_router(internal.router)

    return app
