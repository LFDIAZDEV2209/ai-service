"""Enrutamiento de intenciones del supervisor.

El supervisor decide qué agente/subgrafo atiende cada mensaje.

POR AHORA todo el tráfico va al agente base. Cuando exista la lógica de
negocio, este router se ampliará con clasificación de intención (heurística
o LLM) para enrutar a los subgrafos: rag, doctor, psychologist, crm, etc.
"""

from __future__ import annotations

from app.graph.state import AgentState


class IntentRouter:
    """Selecciona el agente que atiende cada mensaje."""

    def route(self, state: AgentState) -> str:
        """Devuelve la clave del perfil de agente (ver agents/registry.py)."""
        # TODO(lógica de negocio): clasificar intención y seleccionar subgrafo.
        return "base"


router = IntentRouter()
