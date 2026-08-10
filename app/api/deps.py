"""Dependencias FastAPI."""

from functools import lru_cache

from fastapi import HTTPException

from app.core.errors import ConfigError
from app.graph.graph import build_graph


@lru_cache(maxsize=1)
def get_graph():
    """Devuelve el grafo compilado (cacheado; los tests lo sobreescriben vía
    `app.dependency_overrides`)."""
    try:
        return build_graph()
    except ConfigError as exc:
        # Ej: falta la API key del proveedor configurado
        raise HTTPException(
            status_code=503,
            detail=f"Servicio no configurado: {exc}",
        ) from exc
