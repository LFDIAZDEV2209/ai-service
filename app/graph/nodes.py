"""Nodos del grafo supervisor."""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.prebuilt import ToolNode

from app.agents.prompts import BASE_SYSTEM_PROMPT
from app.graph.state import AgentState
from app.safety.guardrails import check_input_guardrails
from app.tools.registry import ALL_TOOLS


def guardrails_node(state: AgentState) -> dict:
    """Nodo de entrada: valida y sanitiza el mensaje del usuario.

    Si es inseguro, termina el turno (el edge condicional lo enruta a END)
    con un mensaje de rechazo. Si es seguro, deja la versión sanitizada en
    `state["input"]` y el flujo continúa al agente.
    """
    result = check_input_guardrails(state.get("input", ""))

    if not result.safe:
        return {
            "guardrail": {
                "safe": False,
                "reason": result.reason,
                "pattern": result.pattern,
            },
            "messages": [
                AIMessage(content=result.reason or "Mensaje bloqueado por los guardrails.")
            ],
        }

    return {
        "input": result.sanitized,
        "guardrail": {"safe": True, "pattern": None, "sanitized": result.sanitized},
    }


def make_agent_node(
    model: BaseChatModel,
    system_prompt: str = BASE_SYSTEM_PROMPT,
) -> Callable[[AgentState], dict]:
    """Crea el nodo que invoca al LLM con las tools enlazadas (`bind_tools`)."""
    bound_model = model.bind_tools(ALL_TOOLS)

    async def agent_node(state: AgentState) -> dict:
        history = list(state.get("messages", []))
        prompt = [
            SystemMessage(content=system_prompt),
            *history,
            HumanMessage(content=state.get("input", "")),
        ]
        response = await bound_model.ainvoke(prompt)
        return {
            # Persistimos el mensaje del usuario (sanitizado) + la respuesta,
            # para que el checkpointer conserve el historial completo.
            "messages": [HumanMessage(content=state.get("input", "")), response],
            "provider": model.__class__.__name__,
        }

    return agent_node


def tools_node(state: AgentState) -> dict:
    """Ejecuta las tools pedidas por el último AIMessage y registra cuáles se usaron."""
    last = state.get("messages", [None])[-1]
    tool_names: list[str] = []
    if last is not None and getattr(last, "tool_calls", None):
        tool_names = [tc.get("name", "?") for tc in last.tool_calls]

    result = ToolNode(ALL_TOOLS).invoke(state)
    return {**result, "tools_used": tool_names}
