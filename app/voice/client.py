"""Cliente ElevenLabs para sesiones de voz (signed URL).

Única pieza que conoce la API key. El signed URL permite que el paciente
conecte al WebSocket de ElevenLabs sin exponer credenciales (mecanismo
oficial vigente; ver docs/elevenlabs/). Cualquier error de la plataforma se
traduce a `VoiceSessionError` con mensaje seguro (sin filtrar detalles
internos ni el contenido del signed URL).
"""

import logging
from urllib.parse import urlencode

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

# Endpoint oficial vigente (docs/api-reference/conversations/get-signed-url).
_GET_SIGNED_URL_PATH = "/v1/convai/conversation/get-signed-url"


class VoiceSessionError(Exception):
    """Fallo al emitir una sesión de voz.

    `safe_message` es lo único que puede viajar al backend/cliente; el
    detalle interno queda en logs sin datos sensibles (nunca la key, nunca el
    signed URL completo).
    """

    def __init__(self, safe_message: str, *, status: int | None = None) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message
        self.status = status


async def create_signed_url(
    settings: Settings,
    agent_id: str,
) -> dict[str, str | None]:
    """Emite un signed URL de vida corta contra ElevenLabs.

    Args:
        settings: configuración del servicio (key, base url, timeout).
        agent_id: agente ElevenLabs de destino (allowlist del endpoint).

    Returns:
        {"signed_url": str, "conversation_id": str | None}.

    Raises:
        VoiceSessionError: si ElevenLabs rechaza la emisión o falla la red.
    """
    query = urlencode(
        {
            "agent_id": agent_id,
            # Conversación one-shot: el conversation_id queda reservado para
            # esta sesión (trazabilidad); el signed URL no es reutilizable.
            "include_conversation_id": "true",
        }
    )
    url = f"{settings.elevenlabs_base_url.rstrip('/')}{_GET_SIGNED_URL_PATH}?{query}"

    try:
        async with httpx.AsyncClient(timeout=settings.elevenlabs_timeout) as client:
            resp = await client.get(url, headers={"xi-api-key": settings.elevenlabs_api_key or ""})
    except httpx.HTTPError as exc:
        # Error de red/timeout: detalle en log, mensaje seguro al caller.
        logger.error("Voice session: fallo de red contra ElevenLabs: %s", type(exc).__name__)
        raise VoiceSessionError(
            "No se pudo contactar a ElevenLabs para iniciar la sesión de voz."
        ) from exc

    if resp.status_code != 200:
        logger.error(
            "Voice session: ElevenLabs rechazó el signed URL (status=%s)",
            resp.status_code,
        )
        if resp.status_code in (401, 403):
            raise VoiceSessionError(
                "Credenciales de ElevenLabs inválidas o sin permiso para el agente.",
                status=resp.status_code,
            )
        if resp.status_code == 404:
            raise VoiceSessionError(
                "El agente de voz configurado no existe en la cuenta de ElevenLabs.",
                status=404,
            )
        # >=500 de la plataforma: es un upstream caído → al backend/cliente via
        # 502 (Bad Gateway), no el 500 crudo del proveedor.
        raise VoiceSessionError(
            "ElevenLabs rechazó la emisión de la sesión de voz.",
            status=502 if resp.status_code >= 500 else resp.status_code,
        )

    try:
        body = resp.json()
    except ValueError as exc:
        logger.error("Voice session: respuesta no JSON de ElevenLabs")
        raise VoiceSessionError("Respuesta inválida de ElevenLabs.") from exc

    signed_url = body.get("signed_url")
    if not signed_url:
        logger.error("Voice session: respuesta de ElevenLabs sin signed_url")
        raise VoiceSessionError("Respuesta inválida de ElevenLabs.")

    # Nunca loguear el signed_url (es la credencial temporal del paciente).
    logger.info("Voice session: signed URL emitido (agent_id=%s)", agent_id)
    return {"signed_url": signed_url, "conversation_id": body.get("conversation_id")}
