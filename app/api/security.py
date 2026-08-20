"""Seguridad de la API: clave compartida para el canal interno backend → AI.

El frontend NUNCA llama al AI Service: todos los endpoints funcionales
(`/chat`, `/threads`, `/agents`, `/admin/*`, `/ingest`) exigen el header
`X-Internal-Key` que solo conoce el backend .NET. El único endpoint público es
`/health`.

CORS no protege un API server-to-server; la autenticación por clave interna +
restricción de red (producción) son el control real.
"""

from fastapi import Header, HTTPException

from app.core.config import get_settings


def require_internal_key(x_internal_key: str | None = Header(default=None)) -> None:
    """Valida el header `X-Internal-Key` contra la configuración compartida.

    - Clave no configurada → 503 (el servicio no está listo para servir).
    - Clave ausente o incorrecta → 401 (nunca revela cuál es la esperada).
    """
    expected = get_settings().internal_api_key
    if not expected:
        raise HTTPException(status_code=503, detail="internal_api_key no configurada")
    if not x_internal_key or x_internal_key != expected:
        raise HTTPException(status_code=401, detail="Clave interna inválida")
