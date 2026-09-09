"""Herramienta de sugerencia de agenda de cita (CTA estructurado).

Genera una sugerencia de acción ("Agenda tu cita aquí") que la app móvil del
paciente renderiza como botón. La tool NO agenda nada: solo emite la
sugerencia estructurada para que la capa de presentación la muestre.

El resultado es un JSON serializado (el ToolMessage queda parseable); el nodo
de tools del grafo lo convierte en `state["suggestions"]`.
"""

from __future__ import annotations

import json

from langchain_core.tools import tool

from app.core.errors import ToolExecutionError

_VALID_URGENCIES = {"normal", "alta"}
_DEFAULT_CTA_TEXT = "Agenda tu cita aquí"


@tool
def suggest_appointment(
    reason: str,
    urgency: str = "normal",
    cta_text: str = _DEFAULT_CTA_TEXT,
) -> str:
    """Sugiere al paciente agendar una cita con un profesional de la salud.

    Usá esta herramienta SOLO cuando la situación del paciente amerite
    revisión profesional: síntomas persistentes, dolor o molestias, malestar
    emocional o afectivo, barreras reportadas, baja adherencia al plan, o
    solicitud explícita de ver a un médico o agendar una cita. Nunca la uses
    en conversaciones casuales o meramente informativas.

    Args:
        reason: motivo breve de la sugerencia en español (por qué se sugiere
            la cita), ej: "Dolor de cabeza persistente hace más de una semana".
        urgency: nivel de urgencia de la sugerencia: "normal" (default) o "alta".
        cta_text: texto del botón de acción (default "Agenda tu cita aquí").
    """
    if urgency not in _VALID_URGENCIES:
        raise ToolExecutionError(f"Urgencia inválida: {urgency!r}. Usa 'normal' o 'alta'.")
    return json.dumps(
        {
            "type": "appointment",
            "cta_text": cta_text or _DEFAULT_CTA_TEXT,
            "reason": reason,
            "urgency": urgency,
        },
        ensure_ascii=False,
    )
