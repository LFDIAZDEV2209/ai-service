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
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
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
    profile_resolver: Any | None = None,
    max_tool_calls: int = 8,
    tool_timeout_seconds: float = 3,
    prepare_tools: list[str] | None = None,
):
    """Construye y compila el grafo supervisor.

    Args:
        model: modelo LLM (por defecto: el de la fábrica multi-proveedor).
        checkpointer: checkpointer de LangGraph (obligatorio en producción;
            resolverlo vía `await get_checkpointer()` en deps/registry — es
            async porque AsyncPostgresSaver necesita un loop abierto).
        system_prompt: prompt de sistema del agente principal (por defecto).
        tools: tools a exponer al agente; None → todas las registradas.
        enable_memory: si True, inserta los nodos de memoria de largo plazo
            (load antes del agente, save después del turno).
        profile_resolver: opcional. Callable que recibe el estado y devuelve el
            `AgentProfile` activo (routing por intención). Si se omite, el
            agente usa siempre `system_prompt`/`tools` (perfil `base`).
    Returns:
        Grafo compilado (acepta `.invoke`, `.ainvoke`, `.astream`).
    """
    if prepare_tools is not None:
        return _build_final_graph(
            model=model or get_chat_model(temperature=None),
            checkpointer=checkpointer,
            system_prompt=system_prompt,
            tools=tools or [],
            prepare_tools=prepare_tools,
            enable_memory=enable_memory,
            max_tool_calls=max_tool_calls,
            tool_timeout_seconds=tool_timeout_seconds,
        )
    workflow = StateGraph(AgentState)

    workflow.add_node("guardrails", guardrails_node)
    workflow.add_node(
        "agent",
        # temperature=None explícito: los modelos thinking de Anthropic
        # rechazan el kwarg (400 "Extra inputs are not permitted" en cada
        # turno del chat de la app); el patrón None explícito es el mismo que
        # usan content_translation/program_narrative/assessment_analysis.
        make_agent_node(
            model or get_chat_model(temperature=None), system_prompt, tools, profile_resolver
        ),
    )
    workflow.add_node(
        "tools", make_tools_node(tools, profile_resolver, max_tool_calls, tool_timeout_seconds)
    )

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
    workflow.add_conditional_edges(
        "tools",
        lambda state: END if isinstance(state["messages"][-1], AIMessage) else "agent",
        {END: END, "agent": "agent"},
    )
    if enable_memory:
        workflow.add_edge("memory_save", END)

    if checkpointer is None:
        raise ValueError(
            "build_graph requiere un checkpointer: resuélvelo con "
            "`await get_checkpointer()` (async) o pasa MemorySaver en tests."
        )
    return workflow.compile(checkpointer=checkpointer)


def _build_final_graph(
    *,
    model,
    checkpointer,
    system_prompt,
    tools,
    prepare_tools,
    enable_memory,
    max_tool_calls,
    tool_timeout_seconds,
):
    """Lecturas decididas en código → respuesta sin tools que sí puede emitirse en vivo.

    Reutiliza guardrails, herramientas, memoria, prompt y checkpoint comunes. No
    necesita un planificador LLM ni otro modelo que reescriba cada turno de voz.
    """
    if checkpointer is None:
        raise ValueError("Se requiere checkpointer explícito")
    workflow = StateGraph(AgentState)
    workflow.add_node("guardrails", guardrails_node)
    workflow.add_node(
        "answer",
        make_agent_node(
            model,
            system_prompt,
            tools=[],
            final_response=True,
        ),
    )
    target = "prepare" if prepare_tools else "answer"
    if prepare_tools:

        def prepare(state):
            return {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": name,
                                "args": {"query": state["input"]}
                                if name == "retrieve_knowledge"
                                else {},
                                "id": f"read-{uuid4()}",
                            }
                            for name in prepare_tools
                        ],
                    )
                ]
            }

        workflow.add_node("prepare", prepare)
        workflow.add_node(
            "tools",
            make_tools_node(
                tools,
                max_tool_calls=max_tool_calls,
                tool_timeout_seconds=tool_timeout_seconds,
            ),
        )
        workflow.add_edge("prepare", "tools")
        workflow.add_edge("tools", "answer")
    if enable_memory:
        workflow.add_node("memory_load", make_memory_load_node())
        workflow.add_node("experience_load", make_experience_load_node())
        workflow.add_node("memory_save", make_memory_save_node())
        workflow.add_edge("memory_load", "experience_load")
        workflow.add_edge("experience_load", target)
        workflow.add_edge("answer", "memory_save")
        workflow.add_edge("memory_save", END)
    else:
        workflow.add_edge("answer", END)
    workflow.add_edge(START, "guardrails")
    workflow.add_conditional_edges(
        "guardrails",
        lambda state: (
            END
            if state.get("guardrail", {}).get("safe") is not True
            else "memory_load"
            if enable_memory
            else target
        ),
    )
    return workflow.compile(checkpointer=checkpointer)
