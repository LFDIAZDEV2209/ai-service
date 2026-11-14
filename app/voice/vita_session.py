"""Emisión/revocación del contexto Vita sin alterar la voz legada."""

import time
import uuid
from datetime import UTC, datetime

from app.core.config import get_settings
from app.vita.client import read_catalog
from app.voice.client import create_signed_url
from app.voice.context import sign_context
from app.voice.schemas import VoiceSessionRequest, VoiceSessionResponse
from app.voice.store import VitaVoiceSession, VoiceContextError, voice_store


async def create_vita_session(payload: VoiceSessionRequest) -> VoiceSessionResponse:
    settings = get_settings()
    if (
        not settings.vita_runtime_enabled
        or not settings.vita_voice_transport_verified
        or len(settings.vita_voice_context_secret.encode()) < 32
        or len(settings.vita_voice_service_key.encode()) < 32
        or not settings.vita_elevenlabs_agent_id
        or not (settings.vita_elevenlabs_version_id or "").startswith("agtvrsn_")
    ):
        raise VoiceContextError("vita_voice_unconfigured_or_unverified", 503)
    # BACK verifica activo/consentimiento; la comprobación se repite por turno.
    await read_catalog(payload.user_id)
    upstream = await create_signed_url(
        settings,
        settings.vita_elevenlabs_agent_id,
        settings.vita_elevenlabs_version_id,
    )
    if not upstream.get("conversation_id"):
        raise VoiceContextError("voice_conversation_unbound", 502)
    session_id = str(uuid.uuid4())
    thread_id = payload.thread_id or str(uuid.uuid4())
    expiry = int(time.time()) + settings.vita_voice_session_ttl
    record = VitaVoiceSession(
        id=session_id,
        user_id=payload.user_id,
        patient_id=payload.patient_id,
        thread_id=thread_id,
        conversation_id=upstream["conversation_id"],
        language="en" if payload.language == "en" else "es",
        expires_at=datetime.fromtimestamp(expiry, UTC),
        revoked=False,
    )
    await voice_store.create(record)
    return VoiceSessionResponse(
        signed_url=upstream["signed_url"],
        agent_id=settings.vita_elevenlabs_agent_id,
        conversation_id=record.conversation_id,
        session_id=session_id,
        thread_id=thread_id,
        session_token=sign_context(
            session_id,
            expiry,
            settings.vita_voice_context_secret,
        ),
        expires_at=record.expires_at,
        runtime_mode="vita",
    )
