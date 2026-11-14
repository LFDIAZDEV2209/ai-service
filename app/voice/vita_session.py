"""Emisión/revocación del contexto Vita sin alterar la voz legada."""

import time
import uuid
from datetime import UTC, datetime

from app.core.config import get_settings
from app.history.service import history_service, storage_key
from app.memory.checkpointer import get_checkpointer
from app.vita.client import read_catalog
from app.vita.locking import ThreadBusyError, thread_guard
from app.voice.client import create_signed_url
from app.voice.context import sign_context
from app.voice.schemas import VoiceSessionRequest, VoiceSessionResponse
from app.voice.store import VitaVoiceSession, VoiceContextError, voice_store


async def reserve_binding(
    payload: VoiceSessionRequest,
    upstream: dict,
    *,
    agent_id: str,
    version_id: str | None,
    runtime_mode: str,
) -> VitaVoiceSession:
    if not payload.supports_vita_voice:
        raise VoiceContextError("voice_history_opt_in_required", 403)
    if not upstream.get("conversation_id"):
        raise VoiceContextError("voice_conversation_unbound", 502)
    expiry = int(time.time()) + get_settings().vita_voice_session_ttl
    record = VitaVoiceSession(
        id=str(uuid.uuid4()),
        user_id=payload.user_id,
        patient_id=payload.patient_id,
        thread_id=payload.thread_id or str(uuid.uuid4()),
        conversation_id=upstream["conversation_id"],
        agent_id=agent_id,
        version_id=version_id,
        runtime_mode=runtime_mode,
        language="en" if payload.language == "en" else "es",
        expires_at=datetime.fromtimestamp(expiry, UTC),
        revoked=False,
    )
    # La creación comparte el lock con limpieza/importación; ningún vínculo
    # puede aparecer entre la revocación de sesiones y el borrado del checkpoint.
    try:
        async with thread_guard(storage_key(record.user_id, record.thread_id)):
            await history_service.ensure_locked(
                record.user_id, record.thread_id, await get_checkpointer(), opt_in=True
            )
            await history_service.activity_locked(record.user_id, record.thread_id)
            await voice_store.create(record)
    except ThreadBusyError as exc:
        raise VoiceContextError("vita_thread_busy", 409) from exc
    return record


async def create_vita_session(payload: VoiceSessionRequest) -> VoiceSessionResponse:
    settings = get_settings()
    if (
        not payload.supports_vita_voice
        or not settings.vita_runtime_enabled
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
    record = await reserve_binding(
        payload,
        upstream,
        agent_id=settings.vita_elevenlabs_agent_id,
        version_id=settings.vita_elevenlabs_version_id,
        runtime_mode="vita",
    )
    return VoiceSessionResponse(
        signed_url=upstream["signed_url"],
        agent_id=settings.vita_elevenlabs_agent_id,
        conversation_id=record.conversation_id,
        session_id=record.id,
        thread_id=record.thread_id,
        session_token=sign_context(
            record.id,
            int(record.expires_at.timestamp()),
            settings.vita_voice_context_secret,
        ),
        expires_at=record.expires_at,
        runtime_mode="vita",
    )
