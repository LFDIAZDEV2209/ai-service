"""Tests del agente del canal WhatsApp (perfil `whatsapp`).

El canal WhatsApp no ofrece agendamiento de citas (la gestión con los
profesionales vive en la app ANTARES) y usa formato de mensajería. La app
móvil conserva el alcance y las tools de citas intactos.
"""

from app.agents.prompts import (
    BASE_SYSTEM_PROMPT,
    PATIENT_SCOPE_POLICY,
    WHATSAPP_SCOPE_POLICY,
    WHATSAPP_SYSTEM_PROMPT,
    build_turn_policy,
)
from app.agents.registry import get_agent_profile


def test_perfil_whatsapp_sin_tools_de_citas():
    profile = get_agent_profile("whatsapp")
    assert "suggest_appointment" not in profile.tools
    assert "mark_control_declined" not in profile.tools
    assert "get_patient_data" not in profile.tools


def test_perfil_base_conserva_tools_de_citas():
    # El canal de la app móvil no cambia: sugerir cita sigue disponible.
    profile = get_agent_profile("base")
    assert profile.tools == ()  # () → todas las registradas


def test_alcance_whatsapp_sin_oferta_de_agendamiento():
    # La línea ALLOWED de citas de la app desaparece en la variante WhatsApp.
    assert (
        "Help contact or book the healthcare team with the available appointment tools"
        not in WHATSAPP_SCOPE_POLICY
    )
    assert "Do not book, promise or offer appointments in this channel" in WHATSAPP_SCOPE_POLICY
    assert "appointments remain available through the app" not in WHATSAPP_SCOPE_POLICY
    # El resto del alcance (emergencias, alcance educativo) permanece.
    assert "EMERGENCY CONTACT GUIDANCE" in WHATSAPP_SCOPE_POLICY
    assert "Do not diagnose" in WHATSAPP_SCOPE_POLICY


def test_alcance_app_intacto():
    # El texto canónico de la app no cambia.
    assert (
        "Help contact or book the healthcare team with the available appointment tools"
        in PATIENT_SCOPE_POLICY
    )


def test_prompt_whatsapp_prohibe_citas_y_fija_formato():
    assert "NO agenda, coordina ni promete citas" in WHATSAPP_SYSTEM_PROMPT
    assert "FORMATO DEL MENSAJE (WhatsApp)" in WHATSAPP_SYSTEM_PROMPT
    # No instruye ofrecer la herramienta de citas (a diferencia del prompt base).
    assert "ofrece la herramienta de la app" not in WHATSAPP_SYSTEM_PROMPT
    assert "ofrece la herramienta de la app" in BASE_SYSTEM_PROMPT


def test_politica_de_turno_sin_agendamiento():
    es = build_turn_policy("es", allow_appointment=False)
    en = build_turn_policy("en", allow_appointment=False)
    assert "agendar" not in es
    assert "contact or book" not in en  # no se instruye agendar en WhatsApp
    assert "Este canal no agenda citas" in es
    assert "This channel does not offer booking" in en


def test_politica_de_turno_default_conserva_agendamiento():
    # La app móvil mantiene el comportamiento actual (compatibilidad).
    assert "contactar o agendar" in build_turn_policy("es")
    assert "contact or book" in build_turn_policy("en")
