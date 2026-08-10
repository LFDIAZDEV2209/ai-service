"""Configuración de logging estructurado para todo el servicio."""

import logging
import sys


def setup_logging(level: str | None = None) -> None:
    """Configura el logging raíz una sola vez (idempotente)."""
    from app.core.config import get_settings

    settings = get_settings()
    level = (level or settings.log_level).upper()

    root = logging.getLogger()
    if root.handlers:  # ya configurado
        root.setLevel(level)
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)
    root.setLevel(level)

    # Silenciar logs ruidosos de librerías
    for noisy in ("httpx", "httpcore", "urllib3", "openai", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
