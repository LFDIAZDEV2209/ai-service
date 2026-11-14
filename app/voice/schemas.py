"""Schemas del módulo de voz (canal interno backend → AI Service).

El payload viaja server-to-server con X-Internal-Key: los identificadores
(user_id, patient_id) provienen del JWT del backend, nunca del cliente.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class VoiceSessionRequest(BaseModel):
    """Solicitud de sesión de voz emitida por el backend .NET."""

    # Identidad del solicitante (derivada del JWT por el backend, no del body
    # del paciente). Sirve para auditoría; el agente no la recibe.
    user_id: str = Field(min_length=1)
    patient_id: str | None = None
    # Hilo del asistente de chat (`proactive-<id>`) para correlacionar la
    # conversación de voz con la sesión de chat del paciente.
    thread_id: str | None = Field(default=None, min_length=1, max_length=128)
    # Idioma de la app: elige una versión publicada/verificada, nunca cambia
    # el agente compartido. Ausente/desconocido usa la versión española.
    language: str | None = None


class VoiceSessionResponse(BaseModel):
    """Sesión temporal de voz lista para conectar desde la app."""

    # Signed URL de vida corta: el cliente se conecta directo al WebSocket de
    # ElevenLabs con él. Caduca solo; no se persiste ni se loguea completo.
    signed_url: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    # conversation_id ya asignado cuando include_conversation_id=true (solo
    # puede usarse una vez; útil para observabilidad end-to-end).
    conversation_id: str | None = None
    thread_id: str | None = None
    session_id: str | None = None
    session_token: str | None = None
    expires_at: datetime | None = None
    runtime_mode: Literal["legacy", "vita"] | None = None


class VoiceDisabledResponse(BaseModel):
    """Respuesta cuando el módulo de voz está apagado en este entorno."""

    detail: str = Field(min_length=1)
