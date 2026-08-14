"""Fábrica de checkpointers de LangGraph (async).

- Desarrollo: `MemorySaver` (en memoria — se pierde al reiniciar).
- Producción: `AsyncPostgresSaver` cuando `DATABASE_URL` está configurada
  (persistencia real, multi-turno, crash recovery y escala horizontal).

IMPORTANTE:
- Se usa `AsyncPostgresSaver` (no el síncrono): el grafo se ejecuta con
  `ainvoke`/`astream` y LangGraph llama a `aget_tuple`/`aput` — el saver
  síncrono no los implementa (`NotImplementedError`).
- `AsyncConnectionPool` exige un event loop corriendo para abrirse, por eso
  `get_checkpointer` es async y se resuelve desde las dependencias del
  request (`get_graph`) o el runtime registry — nunca desde código síncrono.
- El pool y el saver se cachean por proceso (singleton).
"""

from __future__ import annotations

import logging

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_checkpointer = None
_pool = None


async def get_checkpointer():
    """Devuelve el checkpointer según configuración (singleton async)."""
    global _checkpointer, _pool
    if _checkpointer is not None:
        return _checkpointer

    settings = get_settings()

    if settings.database_url:
        try:
            from langgraph.checkpoint.postgres.aio import (
                AsyncConnectionPool,
                AsyncPostgresSaver,
            )
            from psycopg.rows import dict_row

            # Pool lazy: `open=False` evita el error "no running loop" en el
            # constructor; se abre explícitamente aquí (dentro del loop).
            # `search_path=ai,public` hace que las tablas del checkpointer
            # (checkpoints, checkpoint_blobs, ...) vivan en el schema `ai`.
            _pool = AsyncConnectionPool(
                settings.database_url,
                kwargs={
                    "autocommit": True,
                    "prepare_threshold": 0,
                    "row_factory": dict_row,
                    "options": "-c search_path=ai,public",
                },
                name="langgraph-checkpointer",
                open=False,
            )
            await _pool.open()
            _checkpointer = AsyncPostgresSaver(_pool)
            await _checkpointer.setup()  # crea las tablas si no existen
            logger.info(
                "Checkpointer: AsyncPostgresSaver (persistencia real, schema ai)"
            )
            return _checkpointer
        except Exception as exc:
            logger.error("No se pudo iniciar AsyncPostgresSaver: %s", exc)
            raise

    from langgraph.checkpoint.memory import MemorySaver

    logger.warning("Checkpointer: MemorySaver (en memoria — solo desarrollo).")
    _checkpointer = MemorySaver()
    return _checkpointer
