"""Ensamblado del grafo supervisor.

Flujo:
    START → guardrails → (inseguro → END) → agent ⇄ tools → END

El supervisor actual es un agente reactivo con guardrails. Los sub-agentes
especializados (RAG, datos, doctor, CRM...) se conectarán aquí como subgrafos
cuando exista la lógica de negocio (ver app/orchestration/router.py).
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from app.agents.prompts import BASE_SYSTEM_PROMPT
from app.graph.nodes import guardrails_node, make_agent_node, tools_node
from app.graph.state import AgentState
from app.llm.factory import get_chat_model
from app.memory.checkpointer import get_checkpointer


def _route_after_guardrails(state: AgentState) -> str:
    return "agent" if state.get("guardrail", {}).get("safe") is True else END


def _route_after_agent(state: AgentState) -> str:
    last = state.get("messages", [None])[-1]
    if last is not None and getattr(last, "tool_calls", None):
        return "tools"
    return END


def build_graph(
    *,
    model: BaseChatModel | None = None,
    checkpointer: Any = None,
    system_prompt: str = BASE_SYSTEM_PROMPT,
):
    """Construye y compila el grafo supervisor.

    Args:
        model: modelo LLM (por defecto: el de la fábrica multi-proveedor).
        checkpointer: checkpointer de LangGraph (por defecto: el de `get_checkpointer()`).
        system_prompt: prompt de sistema del agente principal.

    Returns:
        Grafo compilado (acepta `.invoke`, `.ainvoke`, `.astream`).
    """
    workflow = StateGraph(AgentState)

    workflow.add_node("guardrails", guardrails_node)
    workflow.add_node("agent", make_agent_node(model or get_chat_model(), system_prompt))
    workflow.add_node("tools", tools_node)

    workflow.add_edge(START, "guardrails")
    workflow.add_conditional_edges(
        "guardrails",
        _route_after_guardrails,
        {"agent": "agent", END: END},
    )
    workflow.add_conditional_edges(
        "agent",
        _route_after_agent,
        {"tools": "tools", END: END},
    )
    workflow.add_edge("tools", "agent")

    return workflow.compile(checkpointer=checkpointer or get_checkpointer())
