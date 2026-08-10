"""Fábrica de checkpointers de LangGraph.

- Desarrollo: `MemorySaver` (en memoria — se pierde al reiniciar).
- Producción: `PostgresSaver` cuando `DATABASE_URL` está configurada
  (persistencia real, multi-turno, crash recovery y escala horizontal).
"""

import logging

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def get_checkpointer():
    """Devuelve el checkpointer según configuración."""
    settings = get_settings()

    if settings.database_url:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver

            # Nota: `from_conn_string` abre un pool que vive durante el proceso.
            # Para cierre explícito usar el patrón context manager:
            #   with PostgresSaver.from_conn_string(...) as cp: ...
            checkpointer = PostgresSaver.from_conn_string(settings.database_url)
            checkpointer.setup()  # crea las tablas necesarias
            logger.info("Checkpointer: PostgresSaver (persistencia real)")
            return checkpointer
        except Exception as exc:
            logger.error("No se pudo iniciar PostgresSaver: %s", exc)
            raise

    from langgraph.checkpoint.memory import MemorySaver

    logger.warning("Checkpointer: MemorySaver (en memoria — solo desarrollo).")
    return MemorySaver()
