"""Endpoint interno de sesiones de voz (backend .NET → AI Service).

Autenticación: X-Internal-Key (mismo patrón que /internal/agents). El backend
valida el JWT del paciente, audita y luego llama aquí; este endpoint emite el
signed URL y devuelve identificadores seguros. Nunca se registra el signed
URL ni la API key en logs.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.security import require_internal_key
from app.core.config import get_settings
from app.voice.client import VoiceSessionError, create_signed_url
from app.voice.schemas import VoiceSessionRequest, VoiceSessionResponse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[Depends(require_internal_key)],
)


@router.post("/voice/session", response_model=VoiceSessionResponse)
async def create_voice_session(payload: VoiceSessionRequest) -> VoiceSessionResponse:
    """Emite una sesión temporal de voz para un paciente autenticado.

    El backend valida identidad/estado/rate limit/auditoría ANTES de llamar
    aquí; este endpoint solo materializa la sesión contra ElevenLabs.
    """
    settings = get_settings()

    if not settings.voice_enabled:
        logger.warning("Voice session solicitada con VOICE_ENABLED=false")
        raise HTTPException(status_code=503, detail="Voz deshabilitada en este entorno.")
    if not settings.elevenlabs_api_key or not settings.elevenlabs_agent_id:
        logger.error("Voice session: configuración ElevenLabs incompleta")
        raise HTTPException(
            status_code=503,
            detail="Configuración de voz incompleta (ELEVENLABS_API_KEY / ELEVENLABS_AGENT_ID).",
        )

    try:
        session = await create_signed_url(settings, settings.elevenlabs_agent_id)
    except VoiceSessionError as exc:
        # Mapeo a HTTP: el detalle interno quedó logueado en el cliente.
        status = exc.status or 502
        logger.error(
            "Voice session fallo (user_id=%s, status=%s): %s",
            payload.user_id,
            status,
            exc.safe_message,
        )
        raise HTTPException(status_code=status, detail=exc.safe_message) from exc

    # Auditoría estructurada SIN datos sensibles: ids internos y agentes,
    # nunca el signed URL, la key ni contenido clínico.
    logger.info(
        "Voice session creada (user_id=%s, patient_id=%s, thread_id=%s, agent_id=%s, "
        "conversation_id=%s)",
        payload.user_id,
        payload.patient_id or "-",
        payload.thread_id or "-",
        settings.elevenlabs_agent_id,
        session["conversation_id"] or "-",
    )

    return VoiceSessionResponse(
        signed_url=session["signed_url"],
        agent_id=settings.elevenlabs_agent_id,
        conversation_id=session["conversation_id"],
    )
