"""Cliente ElevenLabs para sesiones de voz (signed URL).

Única pieza que conoce la API key. El signed URL permite que el paciente
conecte al WebSocket de ElevenLabs sin exponer credenciales (mecanismo
oficial vigente; ver docs/elevenlabs/). Cualquier error de la plataforma se
traduce a `VoiceSessionError` con mensaje seguro (sin filtrar detalles
internos ni el contenido del signed URL).
"""

import hashlib
import json
import logging
import re
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from app.agents.prompts import VOICE_FIRST_MESSAGES, build_patient_voice_prompt
from app.core.config import Settings

logger = logging.getLogger(__name__)

# Endpoint oficial vigente (docs/api-reference/conversations/get-signed-url).
_GET_SIGNED_URL_PATH = "/v1/convai/conversation/get-signed-url"
# Las herramientas legadas de citas están retiradas. Las lecturas de Vita se
# ejecutan en el runtime autenticado, nunca como client tools del proveedor.
PATIENT_CLIENT_TOOL_NAMES: frozenset[str] = frozenset()

# GET de una versión inmutable. El endpoint de signed URL también recibe esa
# MISMA versión, sin modificar el agente compartido ni aceptar overrides del cliente.


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
    version_id: str | None = None,
) -> dict[str, str | None]:
    """Emite un signed URL de vida corta contra ElevenLabs.

    Args:
        settings: configuración del servicio (key, base url, timeout).
        agent_id: agente ElevenLabs de destino (allowlist del endpoint).
        version_id: versión publicada ya verificada para el idioma y alcance.

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
            # La credencial queda vinculada a la versión que acaba de verificarse.
            **({"version_id": version_id} if version_id else {}),
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

    signed_url = body.get("signed_url") if isinstance(body, dict) else None
    try:
        parsed_url = urlsplit(signed_url) if isinstance(signed_url, str) else None
    except ValueError:
        parsed_url = None
    if (
        not isinstance(signed_url, str)
        or not signed_url.strip()
        or parsed_url is None
        or parsed_url.scheme != "wss"
        or not parsed_url.hostname
        or not (body.get("conversation_id") is None or isinstance(body.get("conversation_id"), str))
    ):
        logger.error("Voice session: respuesta de ElevenLabs inválida")
        raise VoiceSessionError("Respuesta inválida de ElevenLabs.", status=502)

    # ElevenLabs US devuelve el ID reservado dentro del URL aunque el JSON
    # solo contenga signed_url. No inferirlo del SDK/cliente ni inventar un ID.
    try:
        query_ids = parse_qs(parsed_url.query, keep_blank_values=True, max_num_fields=64).get(
            "conversation_id", []
        )
        body_id = body.get("conversation_id")
        if len(query_ids) > 1 or any(
            not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value)
            for value in [*query_ids, *([body_id] if body_id is not None else [])]
        ):
            raise ValueError
        url_id = query_ids[0] if query_ids else None
        if body_id is not None and url_id is not None and body_id != url_id:
            raise ValueError
        conversation_id = body_id if body_id is not None else url_id
    except ValueError as exc:
        logger.error("Voice session: identificador reservado inválido o contradictorio")
        raise VoiceSessionError("Respuesta inválida de ElevenLabs.", status=502) from exc

    # Nunca loguear el signed_url (es la credencial temporal del paciente).
    logger.info("Voice session: signed URL emitido (agent_id=%s)", agent_id)
    return {"signed_url": signed_url, "conversation_id": conversation_id}


def _disabled_overrides(value: object) -> bool:
    """Los flags opcionales ausentes heredan false; tipos desconocidos fallan cerrado."""
    if value is None or value is False:
        return True
    if isinstance(value, dict):
        return all(_disabled_overrides(child) for child in value.values())
    return False


def _validate_patient_version(
    payload: object, agent_id: str, version_id: str, language: str
) -> list[str]:
    """Solo acepta la configuración acotada que este despliegue sabe verificar."""
    valid = isinstance(payload, dict)
    if valid:
        configuration = payload.get("conversation_config")
        platform = payload.get("platform_settings")
        valid = (
            payload.get("agent_id") == agent_id
            and payload.get("version_id") == version_id
            and isinstance(configuration, dict)
            and isinstance(platform, dict)
        )
    if valid:
        agent = configuration.get("agent")
        auth = platform.get("auth")
        valid = (
            isinstance(agent, dict)
            and isinstance(auth, dict)
            and auth.get("enable_auth") is True
            and _disabled_overrides(platform.get("overrides"))
        )
    if valid:
        prompt = agent.get("prompt")
        valid = (
            isinstance(prompt, dict)
            and prompt.get("prompt") == build_patient_voice_prompt(language)
            and agent.get("language") == language
            and agent.get("first_message") == VOICE_FIRST_MESSAGES[language]
            and not configuration.get("language_presets")
            and not payload.get("procedures")
            and not prompt.get("native_mcp_server_ids")
            and not prompt.get("knowledge_base")
            and not prompt.get("tools")
            and (
                prompt.get("rag") is None
                or (
                    isinstance(prompt.get("rag"), dict)
                    and prompt["rag"].get("enabled", False) is False
                )
            )
        )
    if valid:
        # No se acepta una ruta alternativa que sustituya el prompt o derive a
        # otro agente. Las herramientas de citas conservan su autorización en la app.
        workflow = payload.get("workflow")
        workspace = platform.get("workspace_overrides")
        builtin = prompt.get("built_in_tools")
        builtin = {} if builtin is None else builtin
        valid = (
            workflow is None
            or workflow == {}
            or (
                isinstance(workflow, dict)
                and not workflow.get("nodes")
                and not workflow.get("edges")
                and not workflow.get("subgraphs")
                and set(workflow).issubset(
                    {"nodes", "edges", "subgraphs", "prevent_subagent_loops"}
                )
            )
        )
        valid = (
            valid
            and (workspace is None or isinstance(workspace, dict))
            and not (workspace or {}).get("conversation_initiation_client_data_webhook")
            and isinstance(builtin, dict)
            and not any(value is not None for value in builtin.values())
        )
    tool_ids = prompt.get("tool_ids") if valid else None
    tool_ids = [] if tool_ids is None else tool_ids
    valid = (
        valid
        and isinstance(tool_ids, list)
        and all(
            isinstance(tool_id, str) and re.fullmatch(r"tool_[A-Za-z0-9_-]+", tool_id)
            for tool_id in tool_ids
        )
    )
    if not valid or len(set(tool_ids)) != len(tool_ids):
        raise VoiceSessionError("La voz no está disponible en este momento.", status=503)
    return tool_ids


def patient_tool_config_digest(tool_config: dict) -> str:
    """Huella de la definición completa, sin estadísticas/PII mutables de acceso."""
    canonical = json.dumps(
        tool_config, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _verify_patient_tools(client: httpx.AsyncClient, settings: Settings, tool_ids: list[str]):
    for tool_id in tool_ids:
        approved = settings.elevenlabs_patient_tool_config_sha256.get(tool_id, "")
        if not re.fullmatch(r"[0-9a-f]{64}", approved):
            raise VoiceSessionError("La voz no está disponible en este momento.", status=503)
        response = await client.get(
            f"{settings.elevenlabs_base_url.rstrip('/')}/v1/convai/tools/{tool_id}",
            headers={"xi-api-key": settings.elevenlabs_api_key or ""},
        )
        payload = response.json() if response.status_code == 200 else None
        config = payload.get("tool_config") if isinstance(payload, dict) else None
        if (
            not isinstance(config, dict)
            or payload.get("id") != tool_id
            or payload.get("response_mocks")
            or config.get("type") != "client"
            or config.get("name") not in PATIENT_CLIENT_TOOL_NAMES
            or patient_tool_config_digest(config) != approved
        ):
            raise VoiceSessionError("La voz no está disponible en este momento.", status=503)


async def verify_patient_voice_version(
    settings: Settings, agent_id: str, version_id: str, language: str
) -> None:
    """Lee la versión exacta en cada sesión; nunca confía en una caché de otro idioma."""
    url = (
        f"{settings.elevenlabs_base_url.rstrip('/')}/v1/convai/agents/{agent_id}"
        f"?{urlencode({'version_id': version_id})}"
    )
    try:
        async with httpx.AsyncClient(timeout=settings.elevenlabs_timeout) as client:
            response = await client.get(
                url, headers={"xi-api-key": settings.elevenlabs_api_key or ""}
            )
            if response.status_code != 200:
                raise VoiceSessionError("La voz no está disponible en este momento.", status=503)
            tool_ids = _validate_patient_version(response.json(), agent_id, version_id, language)
            await _verify_patient_tools(client, settings, tool_ids)
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        # Nunca incluir la respuesta ni el prompt del proveedor en logs/errores.
        logger.warning("Voice scope verification failed: exception_type=%s", type(exc).__name__)
        raise VoiceSessionError("La voz no está disponible en este momento.", status=503) from exc
