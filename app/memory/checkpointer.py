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
            import psycopg
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg.rows import dict_row

            # Las tablas del checkpointer (checkpoints, checkpoint_blobs, ...)
            # viven en el schema `ai`, junto al resto de tablas del servicio.
            # Se construye con una conexión psycopg propia (el `search_path` la
            # hace crear las tablas en `ai`) porque PostgresSaver.from_conn_string
            # cierra la conexión al salir del context manager y no acepta schema.
            conn = psycopg.connect(
                settings.database_url,
                autocommit=True,
                prepare_threshold=0,
                row_factory=dict_row,
                options="-c search_path=ai,public",
            )
            checkpointer = PostgresSaver(conn)
            checkpointer.setup()  # crea las tablas necesarias
            logger.info("Checkpointer: PostgresSaver (persistencia real, schema ai)")
            return checkpointer
        except Exception as exc:
            logger.error("No se pudo iniciar PostgresSaver: %s", exc)
            raise

    from langgraph.checkpoint.memory import MemorySaver

    logger.warning("Checkpointer: MemorySaver (en memoria — solo desarrollo).")
    return MemorySaver()
