"""Punto de entrada del servicio.

Uso (Windows — SIEMPRE por aquí):
    python main.py          # host 0.0.0.0, reload en development
    python run_dev.py       # alias local: host 127.0.0.1, sin reload

Importante (Windows): uvicorn 0.36+ fuerza `ProactorEventLoop` por defecto
(`uvicorn/loops/asyncio.py`), incompatible con psycopg async. Este runner
centraliza la selección de `SelectorEventLoop` en win32 — nunca lanzar el
servicio con `uvicorn main:app` directo, o toda operación de BD (ingest,
checkpointer, memoria) fallará con psycopg.InterfaceError.
"""

import sys

import uvicorn

from app.api.main import create_app
from app.core.config import get_settings

app = create_app()


def _select_loop() -> str:
    """Fábrica de loop compatible con psycopg async en Windows.

    uvicorn 0.36+ en win32 usa `ProactorEventLoop` vía su `loop_factory`,
    ignorando la política del proceso; el parámetro `loop` de uvicorn.run
    es la única forma fiable de elegir el loop.
    """
    return "asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto"


def run_server(
    *,
    host: str = "0.0.0.0",
    port: int = 8000,
    reload: bool | None = None,
) -> None:
    """Arranca uvicorn con el event loop correcto (fuente única de verdad).

    Args:
        host: interfaz de escucha (0.0.0.0 por defecto).
        port: puerto de escucha.
        reload: recarga automática en cambios (None → entorno de desarrollo).
    """
    settings = get_settings()
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=settings.environment == "development" if reload is None else reload,
        loop=_select_loop(),
    )


if __name__ == "__main__":
    run_server()
