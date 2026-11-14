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
    assert "Appointments are unavailable" in WHATSAPP_SCOPE_POLICY
    assert "appointments remain available through the app" not in WHATSAPP_SCOPE_POLICY
    # El resto del alcance (emergencias, alcance educativo) permanece.
    assert "EMERGENCY CONTACT GUIDANCE" in WHATSAPP_SCOPE_POLICY
    assert "Do not diagnose" in WHATSAPP_SCOPE_POLICY


def test_alcance_app_sin_citas():
    assert "Appointments are unavailable" in PATIENT_SCOPE_POLICY
    assert "Help contact or book" not in PATIENT_SCOPE_POLICY


def test_prompt_whatsapp_prohibe_citas_y_fija_formato():
    assert "NO agenda, coordina ni promete citas" in WHATSAPP_SYSTEM_PROMPT
    assert "FORMATO DEL MENSAJE (WhatsApp)" in WHATSAPP_SYSTEM_PROMPT
    # No instruye ofrecer la herramienta de citas (a diferencia del prompt base).
    assert "ofrece la herramienta de la app" not in WHATSAPP_SYSTEM_PROMPT
    assert "ofrece la herramienta de la app" not in BASE_SYSTEM_PROMPT


def test_politica_de_turno_sin_agendamiento():
    es = build_turn_policy("es", allow_appointment=False)
    en = build_turn_policy("en", allow_appointment=False)
    assert "No ofrezcas agendar" in es
    assert "contact or book" not in en  # no se instruye agendar en WhatsApp
    assert "Las citas no están disponibles" in es
    assert "Appointments are unavailable" in en


def test_politica_de_turno_default_sin_citas():
    assert "No ofrezcas agendar" in build_turn_policy("es")
    assert "Never offer booking" in build_turn_policy("en")


def test_tools_de_perfiles_existen_en_el_registro():
    from app.agents.registry import AGENTS
    from app.tools.registry import TOOLS_BY_NAME

    for key, profile in AGENTS.items():
        for name in profile.tools:
            assert name in TOOLS_BY_NAME, f"{key}: tool '{name}' no existe en el registro"


async def test_grafo_resuelve_tools_por_nombre_a_objetos():
    """Regresión: el perfil whatsapp declara tools por nombre; bind_tools exige
    BaseTool. El proveedor real rechaza strings ("Unsupported function")."""
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import MemorySaver

    from app.graph.graph import build_graph
    from tests.fakes import StrictBindFakeModel

    model = StrictBindFakeModel(responses=[AIMessage(content="Hola, ¿en qué te ayudo?")])

    async def resolve(_):
        return get_agent_profile("whatsapp")

    graph = build_graph(model=model, checkpointer=MemorySaver(), profile_resolver=resolve)
    result = await graph.ainvoke(
        {"input": "Hola", "messages": []},
        config={"configurable": {"thread_id": "whatsapp-tools-regression"}},
    )
    assert "Hola" in result["messages"][-1].content
