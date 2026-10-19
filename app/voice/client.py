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
# PATCH de agente (docs/api-reference/agents/update-agent): actualiza la
# configuración server-side del agente (idioma/first_message) ANTES de emitir
# el signed URL — con sesiones por signed URL los overrides del cliente NO
# aplican, así que el idioma de la voz se resuelve aquí.
_UPDATE_AGENT_PATH = "/v1/convai/agents/{agent_id}"

# Saludo inicial localizado por idioma (conversation_config.agent.first_message).
_FIRST_MESSAGES = {
    "es": "Hola, soy el asistente de Copp Adresd. ¿Cómo te puedo ayudar con "
    "tus citas o tu atención de hoy?",
    "en": "Hi, I'm the Copp Adresd assistant. How can I help you with your "
    "appointments or care today?",
}


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
            # Permite que el cliente envíe conversation_initiation_client_data
            # (overrides.agent.language: idioma de la app 'es' | 'en'). Sin este
            # flag ElevenLabs CIERRA la conexión si el cliente manda overrides.
            "enable_conversation_config_override": "true",
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


async def set_agent_language(settings: Settings, agent_id: str, language: str) -> None:
    """Deja el agente de voz hablando el idioma de la app para la próxima sesión.

    PATCH parcial de `conversation_config.agent` (idioma + saludo inicial en
    ese idioma) contra ElevenLabs ANTES de emitir el signed URL: con sesiones
    por signed URL los overrides del cliente no aplican, así que el idioma de
    la voz se resuelve server-side.

    Elección de diseño (decidida con el usuario): UN agente mutado por
    sesión — simple, sin duplicar agentes en el dashboard; a cambio, dos
    sesiones concurrentes en idiomas distintos pueden competir por la config
    (carrera benigna: la sesión lee la config al conectar).

    Args:
        settings: configuración del servicio (key, base url, timeout).
        agent_id: agente ElevenLabs de destino.
        language: idioma normalizado ('es' | 'en'); otro valor no cambia nada.

    Fail-open: un fallo del PATCH NUNCA rompe la sesión de voz — se loguea y
    la sesión continúa con el idioma vigente del agente (degradación igual a
    la conducta histórica).
    """
    if language not in _FIRST_MESSAGES or not settings.elevenlabs_api_key:
        return

    base = settings.elevenlabs_base_url.rstrip("/")
    url = f"{base}{_UPDATE_AGENT_PATH.format(agent_id=agent_id)}"
    payload = {
        "conversation_config": {
            "agent": {
                "language": language,
                "first_message": _FIRST_MESSAGES[language],
            }
        }
    }

    try:
        async with httpx.AsyncClient(timeout=settings.elevenlabs_timeout) as client:
            resp = await client.patch(
                url,
                json=payload,
                headers={"xi-api-key": settings.elevenlabs_api_key},
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "Voice session: fallo de red PATCH idioma agente (%s); la sesión "
            "continúa con el idioma vigente",
            type(exc).__name__,
        )
        return

    if resp.status_code >= 400:
        # Nunca incluir el body de respuesta (podría reflejar la key).
        logger.warning(
            "Voice session: PATCH idioma agente rechazado (status=%s); la "
            "sesión continúa con el idioma vigente",
            resp.status_code,
        )
        return

    logger.info("Voice session: agente configurado en idioma=%s", language)
