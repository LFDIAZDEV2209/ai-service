"""Ensamblado del grafo supervisor.

Flujo:
    START → guardrails → (inseguro → END) → agent ⇄ tools → END

Con memoria habilitada (`enable_memory=True`):
    START → guardrails → memory_load → agent ⇄ tools → memory_save → END

El supervisor actual es un agente reactivo con guardrails. Los sub-agentes
especializados (RAG, datos, doctor, CRM...) se conectarán aquí como subgrafos
cuando exista la lógica de negocio (ver app/orchestration/router.py).
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph

from app.agents.prompts import BASE_SYSTEM_PROMPT
from app.graph.nodes import (
    guardrails_node,
    make_agent_node,
    make_experience_load_node,
    make_memory_load_node,
    make_memory_save_node,
    make_tools_node,
)
from app.graph.state import AgentState
from app.llm.factory import get_chat_model
from app.memory.checkpointer import get_checkpointer


def _make_route_after_guardrails(enable_memory: bool) -> Any:
    def _route(state: AgentState) -> str:
        if state.get("guardrail", {}).get("safe") is not True:
            return END
        return "memory_load" if enable_memory else "agent"

    return _route


def _make_route_after_agent(enable_memory: bool) -> Any:
    def _route_after_agent(state: AgentState) -> str:
        last = state.get("messages", [None])[-1]
        if last is not None and getattr(last, "tool_calls", None):
            return "tools"
        return "memory_save" if enable_memory else END

    return _route_after_agent


def build_graph(
    *,
    model: BaseChatModel | None = None,
    checkpointer: Any = None,
    system_prompt: str = BASE_SYSTEM_PROMPT,
    tools: list[BaseTool] | None = None,
    enable_memory: bool = False,
):
    """Construye y compila el grafo supervisor.

    Args:
        model: modelo LLM (por defecto: el de la fábrica multi-proveedor).
        checkpointer: checkpointer de LangGraph (por defecto: el de `get_checkpointer()`).
        system_prompt: prompt de sistema del agente principal.
        tools: tools a exponer al agente; None → todas las registradas.
        enable_memory: si True, inserta los nodos de memoria de largo plazo
            (load antes del agente, save después del turno).

    Returns:
        Grafo compilado (acepta `.invoke`, `.ainvoke`, `.astream`).
    """
    workflow = StateGraph(AgentState)

    workflow.add_node("guardrails", guardrails_node)
    workflow.add_node("agent", make_agent_node(model or get_chat_model(), system_prompt, tools))
    workflow.add_node("tools", make_tools_node(tools))

    if enable_memory:
        workflow.add_node("memory_load", make_memory_load_node())
        workflow.add_node("experience_load", make_experience_load_node())
        workflow.add_node("memory_save", make_memory_save_node())

    workflow.add_edge(START, "guardrails")
    workflow.add_conditional_edges(
        "guardrails",
        _make_route_after_guardrails(enable_memory),
        {"agent": "agent", "memory_load": "memory_load", END: END}
        if enable_memory
        else {"agent": "agent", END: END},
    )
    if enable_memory:
        workflow.add_edge("memory_load", "experience_load")
        workflow.add_edge("experience_load", "agent")
    workflow.add_conditional_edges(
        "agent",
        _make_route_after_agent(enable_memory),
        {"tools": "tools", "memory_save": "memory_save", END: END}
        if enable_memory
        else {"tools": "tools", END: END},
    )
    workflow.add_edge("tools", "agent")
    if enable_memory:
        workflow.add_edge("memory_save", END)

    return workflow.compile(checkpointer=checkpointer or get_checkpointer())
