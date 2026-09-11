"""Nodos del grafo supervisor."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from contextlib import suppress

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.config import get_config
from langgraph.prebuilt import ToolNode

from app.agents.prompts import BASE_SYSTEM_PROMPT, build_control_guidance
from app.agents.registry import AgentProfile
from app.graph.state import AgentState
from app.memory.adaptive import AdaptiveMemoryService
from app.memory.service import UserMemoryService
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
    tools: list[BaseTool] | None = None,
    profile_resolver: Callable[[AgentState], Awaitable[AgentProfile | None]] | None = None,
) -> Callable[[AgentState], Awaitable[dict]]:
    """Crea el nodo que invoca al LLM con las tools enlazadas (`bind_tools`).

    Args:
        model: modelo de chat a usar.
        system_prompt: prompt de sistema por defecto (se usa si no hay
            `profile_resolver` o el perfil resuelto no trae prompt propio).
        tools: tools a exponer al modelo; None → todas las registradas.
        profile_resolver: opcional. Callable ASYNC que devuelve el
            `AgentProfile` activo para el estado actual (routing por intención).
            Permite que el nodo cambie de "sombrero" (prompt/tools del perfil)
            en cada turno, leyendo la config de la BD cuando corresponda.

    Returns:
        Callable async que ejecuta un turno del agente.
    """

    async def agent_node(state: AgentState) -> dict:
        # Resolución del perfil activo: el precedente gana sobre el default.
        profile = await profile_resolver(state) if profile_resolver else None
        active_prompt = profile.system_prompt if profile else system_prompt
        active_tools = profile.tools if profile and profile.tools else (tools or ALL_TOOLS)
        active_model = model.bind_tools(list(active_tools))

        history = list(state.get("messages", []))
        system_blocks = [active_prompt]

        # Memoria de largo plazo: se inyecta como mensaje de sistema adicional
        # cuando el nodo de memoria la cargó (aislamiento por user_id).
        memory_context = state.get("memory_context")
        if memory_context:
            system_blocks.append(memory_context)

        # Experiencia aprendida del agente: bloque SEPARADO de la memoria del
        # usuario (pertenece al agente, no al paciente).
        experience_context = state.get("experience_context")
        if experience_context:
            system_blocks.append(experience_context)

        # Control de programa (UC-001 'Controles'): guía por turno inyectada
        # SOLO cuando el backend la envía. Viaja por `configurable` (no por
        # state) para que nunca persista en el checkpointer entre turnos: un
        # turno sin contexto no debe heredar la guía del turno anterior.
        configurable = get_config().get("configurable", {})
        control_context = configurable.get("control_context")
        if control_context:
            system_blocks.append(
                build_control_guidance(
                    day=control_context.get("milestone_day", 0),
                    status=control_context.get("status", ""),
                    exam_pending=control_context.get("exam_pending", True),
                )
            )

        prompt = [
            *[SystemMessage(content=block) for block in system_blocks],
            *history,
            HumanMessage(content=state.get("input", "")),
        ]

        response = await active_model.ainvoke(prompt)
        return {
            # Persistimos el mensaje del usuario (sanitizado) + la respuesta,
            # para que el checkpointer conserve el historial completo.
            "messages": [HumanMessage(content=state.get("input", "")), response],
            "provider": model.__class__.__name__,
            "agent": profile.key if profile else state.get("agent", "base"),
        }

    return agent_node


def make_memory_load_node(
    service_factory: Callable[[], UserMemoryService] | None = None,
) -> Callable[[AgentState], Awaitable[dict]]:
    """Crea el nodo que carga la memoria del usuario antes del agente.

    Lee `user_id`/`agent_instance_id` del `configurable` de ejecución
    (propagado por el backend) y `agent_type_id` del estado; si no hay
    contexto de usuario, el nodo no hace nada (memoria deshabilitada).

    Args:
        service_factory: fábrica del servicio de memoria (inyectable en tests
            sin BD); por defecto usa la sesión del engine (`async_session`).
    """

    async def memory_load_node(state: AgentState) -> dict:
        configurable = get_config().get("configurable", {})
        user_id = configurable.get("user_id")
        agent_type_id = state.get("agent")
        if not user_id or not agent_type_id:
            return {}

        service = service_factory() if service_factory is not None else _default_service()
        try:
            context = await service.load_context(
                user_id=user_id,
                agent_type_id=agent_type_id,
                agent_instance_id=configurable.get("agent_instance_id"),
            )
        except Exception:
            # La memoria nunca debe tumbar el chat: sin BD → sin memoria.
            context = ""
        return {"memory_context": context}

    return memory_load_node


def make_memory_save_node(
    service_factory: Callable[[], UserMemoryService] | None = None,
) -> Callable[[AgentState], Awaitable[dict]]:
    """Crea el nodo que persiste hechos y resumen tras el turno del agente.

    Extrae hechos declarativos del mensaje del usuario y rota el resumen
    rodante si el historial lo amerita. Nunca falla el turno por errores de
    persistencia.
    """

    async def memory_save_node(state: AgentState) -> dict:
        configurable = get_config().get("configurable", {})
        user_id = configurable.get("user_id")
        agent_type_id = state.get("agent")
        input_text = state.get("input")
        if not user_id or not agent_type_id or not input_text:
            return {}

        service = service_factory() if service_factory is not None else _default_service()
        with suppress(Exception):
            await service.extract_and_save(
                user_id=user_id,
                agent_type_id=agent_type_id,
                agent_instance_id=configurable.get("agent_instance_id"),
                message=input_text,
            )
            # El servicio deja la transacción abierta (commit del llamador);
            # si no se persiste aquí, la sesión se descarta sin guardar nada.
            await service.commit()

        # Historial previo (sin la respuesta del turno) para el resumen rodante.
        history: list[str] = []
        for msg in state.get("messages", []):
            text = getattr(msg, "content", "")
            if isinstance(text, str) and text:
                history.append(text)

        with suppress(Exception):
            last_summary = await service.get_summary(user_id=user_id, agent_type_id=agent_type_id)
            await service.maybe_roll_summary(
                user_id=user_id,
                agent_type_id=agent_type_id,
                agent_instance_id=configurable.get("agent_instance_id"),
                history=history,
                last_summary=last_summary,
            )
            # Persiste también el resumen rodante si se rotó (transacción corta).
            await service.commit()

        return {}

    return memory_save_node


def make_experience_load_node(
    service_factory: Callable[[], AdaptiveMemoryService] | None = None,
) -> Callable[[AgentState], Awaitable[dict]]:
    """Crea el nodo que inyecta la experiencia aprendida del agente.

    Carga los patrones exitosos y recurrentes del `agent_type_id` actual y los
    expone en `experience_context` (bloque SEPARADO de `memory_context`: la
    experiencia pertenece al agente, la memoria al usuario).
    """

    async def experience_load_node(state: AgentState) -> dict:
        agent_type_id = state.get("agent")
        if not agent_type_id:
            return {}

        service = service_factory() if service_factory is not None else _default_adaptive_service()
        try:
            experiences = await service.load_experiences(agent_type_id=agent_type_id)
            context = service.format_experiences(experiences)
        except Exception:
            context = ""
        return {"experience_context": context}

    return experience_load_node


def _default_service() -> UserMemoryService:
    """Servicio de memoria con sesión corta del engine (producción)."""
    from app.db.engine import async_session
    from app.memory.service import UserMemoryService

    return UserMemoryService(async_session())


def _default_adaptive_service() -> AdaptiveMemoryService:
    """Servicio de adaptive memory con sesión corta del engine (producción)."""
    from app.db.engine import async_session

    return AdaptiveMemoryService(async_session())


def make_tools_node(tools: list[BaseTool] | None = None) -> Callable[[AgentState], Awaitable[dict]]:
    """Crea el nodo de ejecución de tools para el subconjunto indicado.

    El wrapper es async a propósito: el `ToolNode` interno es un
    `RunnableCallable` cuyo `__call__` es síncrono; si se expone así, PregelNode
    lo ejecuta por la ruta sync (`_execute_tool_sync`) y las tools async
    (p. ej. `retrieve_knowledge`) fallan con "StructuredTool does not support
    sync invocation". Con `ainvoke` se usa `_execute_tool_async` y las tools
    coroutine funcionan.

    Además de ejecutar las tools, captura las sugerencias estructuradas
    emitidas por `suggest_appointment` (ver `_extract_suggestions`) y las
    acumula en `state["suggestions"]`.
    """
    selected = tools or ALL_TOOLS
    node = ToolNode(selected)

    async def tools_node(state: AgentState) -> dict:
        last = state.get("messages", [None])[-1]
        tool_names: list[str] = []
        if last is not None and getattr(last, "tool_calls", None):
            tool_names = [tc.get("name", "?") for tc in last.tool_calls]

        result = await node.ainvoke(state)
        return {
            **result,
            "tools_used": tool_names,
            "suggestions": _extract_suggestions(result.get("messages", [])),
        }

    return tools_node


def _extract_suggestions(messages: list) -> list[dict]:
    """Extrae las sugerencias estructuradas de los ToolMessages del turno.

    `suggest_appointment` devuelve un JSON serializado; aquí se parsea cada
    ToolMessage de esa tool y se devuelve la lista de dicts para acumular en
    el estado. Los mensajes malformados se descartan en silencio (nunca deben
    tumbar el turno).
    """
    suggestions: list[dict] = []
    for msg in messages:
        if getattr(msg, "name", None) != "suggest_appointment":
            continue
        content = getattr(msg, "content", "")
        if not isinstance(content, str) or not content:
            continue
        with suppress(Exception):
            data = json.loads(content)
            if isinstance(data, dict):
                suggestions.append(data)
    return suggestions


async def tools_node(state: AgentState) -> dict:
    """Nodo por defecto con todas las tools registradas."""
    return await make_tools_node()(state)
