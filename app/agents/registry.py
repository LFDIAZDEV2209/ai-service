"""Registro de perfiles de agente.

Cada perfil define prompt, proveedor/modelo y tools propias. Cuando exista la
lógica de negocio (doctor, psicólogo, CRM, datos de la API .NET...) se agregan
perfiles aquí y se conectan sus subgrafos en `app/graph/graph.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agents.prompts import BASE_SYSTEM_PROMPT


@dataclass(frozen=True)
class AgentProfile:
    key: str
    name: str
    description: str
    system_prompt: str
    provider: str | None = None  # None → usar el proveedor por defecto
    model: str | None = None  # None → usar el modelo del proveedor por defecto
    tools: tuple[str, ...] = ()  # subconjunto de tools; () → todas


AGENTS: dict[str, AgentProfile] = {
    "base": AgentProfile(
        key="base",
        name="Asistente general",
        description="Agente base de conversación con herramientas genéricas.",
        system_prompt=BASE_SYSTEM_PROMPT,
    ),
    # ── Especializados (cuando exista la lógica de negocio) ──────────────
    # "rag":      AgentProfile(key="rag", name="Documentación", ...),
    # "doctor":   AgentProfile(key="doctor", name="Asistente médico", ...),
    # "psychologist": AgentProfile(key="psychologist", ...),
    # "crm":      AgentProfile(key="crm", name="CRM / datos", ...),
}


def get_agent_profile(key: str = "base") -> AgentProfile:
    profile = AGENTS.get(key)
    if profile is None:
        raise KeyError(
            f"Perfil de agente desconocido: {key!r}. Disponibles: {sorted(AGENTS)}"
        )
    return profile
