"""Tool no-op del control de programa: señal de rechazo estructurada.

`mark_control_declined` no muestra ni devuelve nada visible para el paciente:
solo deja una marca detectable post-run en `state["tools_used"]` para que la
ruta de chat emita `control_signal: "declined"` (sync) o el evento SSE
`control_signal` (stream). El paciente solo ve la respuesta cálida del agente.
"""

from __future__ import annotations

from langchain_core.tools import tool


@tool
def mark_control_declined() -> str:
    """Registra que el paciente rechazó explícitamente subir su examen de laboratorio.

    Usá esta herramienta SOLO cuando el paciente se niega de forma clara e
    inequívoca a subir su examen del control de programa. No muestra nada al
    paciente: emite la señal interna para que el sistema cierre el control con
    respeto. Nunca la uses si el paciente solo difiere la subida, duda o pide
    más tiempo.
    """
    return "ok"
