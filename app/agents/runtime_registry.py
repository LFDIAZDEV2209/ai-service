"""Registry de runtime multi-agente.

Compila y cachea el grafo LangGraph por tipo de agente (`agent_type_id`),
usando la configuración sincronizada por el backend en
`ai.agent_runtime_configs`. El cache se invalida cuando el backend activa
una versión nueva (endpoint interno `/internal/agents/sync-config`).

Aislamiento por conversación: cada ejecución pasa `configurable` con
`thread_id`, `user_id`, `patient_id` y `agent_instance_id` para que el
checkpointer y las tools aíslen el estado por usuario/paciente.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runtime_config import AgentRuntimeConfig
from app.db.models import AgentRuntimeConfig as AgentRuntimeConfigRow
from app.graph.graph import build_graph
from app.llm.factory import get_chat_model
from app.memory.checkpointer import get_checkpointer
from app.tools.registry import TOOLS_BY_NAME
from app.tools.retrieval import make_retrieve_tool

logger = logging.getLogger(__name__)

# Tope de grafos cacheados por proceso (evita fuga de memoria si se crean
# muchos tipos de agente; el más antiguo se descarta).
_MAX_CACHED_GRAPHS = 128


class AgentRuntimeError(Exception):
    """Error de resolución/compilación del runtime de un agente."""


@dataclass(frozen=True)
class CompiledAgent:
    """Grafo compilado + metadatos de ejecución para un tipo de agente."""

    agent_type_id: str
    version_id: str
    graph: Any
    runtime_config: AgentRuntimeConfig


class AgentRuntimeRegistry:
    """Compila y cachea grafos por tipo de agente (thread-safe)."""

    def __init__(
        self,
        model_factory: Callable[..., Any] | None = None,
        checkpointer_factory: Callable[[], Any] | None = None,
    ) -> None:
        """Args:
        model_factory: fábrica de modelos LLM (por defecto `get_chat_model`);
            inyectable en tests para usar un fake sin API key.
        checkpointer_factory: fábrica del checkpointer (por defecto
            `get_checkpointer` — async); inyectable en tests para usar
            `MemorySaver` sin Postgres.
        """
        self._model_factory = model_factory or get_chat_model
        self._checkpointer_factory = checkpointer_factory
        self._cache: dict[str, CompiledAgent] = {}
        self._lock = threading.Lock()

    def invalidate(self, agent_type_id: str) -> None:
        """Descarta el grafo cacheado (llamado al sincronizar una versión)."""
        with self._lock:
            removed = self._cache.pop(agent_type_id, None)
        if removed is not None:
            logger.info("Cache de runtime invalidado para agente %s", agent_type_id)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    async def get_agent(
        self,
        agent_type_id: str,
        session: AsyncSession,
    ) -> CompiledAgent:
        """Devuelve el agente compilado, cargándolo bajo demanda si no está cacheado.

        Raises:
            AgentRuntimeError: si el agente no está sincronizado (el backend
                aún no lo notificó) o su configuración es inválida.
        """
        with self._lock:
            cached = self._cache.get(agent_type_id)
        if cached is not None:
            return cached

        return await self._load_and_compile(agent_type_id, session)

    async def _load_and_compile(
        self,
        agent_type_id: str,
        session: AsyncSession,
    ) -> CompiledAgent:
        row = (
            await session.execute(
                select(AgentRuntimeConfigRow).where(
                    AgentRuntimeConfigRow.agent_type_id == agent_type_id,
                    AgentRuntimeConfigRow.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()

        if row is None:
            raise AgentRuntimeError(
                f"Agente {agent_type_id} no sincronizado en el runtime. "
                "Activa una versión desde el backend para precompilarlo."
            )

        runtime_config = AgentRuntimeConfig.model_validate(row.config)
        compiled = await self._compile(agent_type_id, row.version_id or "", runtime_config)

        with self._lock:
            if len(self._cache) >= _MAX_CACHED_GRAPHS:
                # Descarta la entrada más antigua (inserción ordenada).
                oldest = next(iter(self._cache))
                del self._cache[oldest]
                logger.info("Cache de runtime lleno; descartado agente %s", oldest)
            self._cache[agent_type_id] = compiled

        logger.info(
            "Runtime compilado para agente %s (versión %s)",
            agent_type_id,
            compiled.version_id,
        )
        return compiled

    async def _compile(
        self,
        agent_type_id: str,
        version_id: str,
        runtime_config: AgentRuntimeConfig,
    ) -> CompiledAgent:
        """Construye el grafo con los overrides del agente (provider/model/tools)."""
        try:
            model = self._model_factory(
                provider=runtime_config.provider,
                model=runtime_config.model,
                temperature=runtime_config.temperature,
                max_tokens=runtime_config.max_tokens,
            )
        except Exception as exc:
            raise AgentRuntimeError(
                f"Configuración LLM inválida para agente {agent_type_id}: {exc}"
            ) from exc

        tools = self._select_tools(runtime_config.tools, agent_type_id)

        # RAG: si el agente tiene retrieval habilitado Y KBs explícitas, se le
        # inyecta la tool de recuperación limitada a sus KBs (aislamiento entre
        # agentes). `enabled` sin KBs no agrega la tool: un retriever sin filtro
        # recuperaría chunks de KBs ajenas (globales y privadas de otros).
        retrieval = runtime_config.retrieval_config
        if retrieval.enabled and retrieval.knowledge_base_ids:
            tools.append(
                make_retrieve_tool(
                    knowledge_base_ids=retrieval.knowledge_base_ids,
                    top_k=retrieval.top_k,
                )
            )

        graph = build_graph(
            model=model,
            system_prompt=runtime_config.effective_system_prompt(),
            tools=tools,
            enable_memory=runtime_config.memory_config.enabled,
            checkpointer=(
                await get_checkpointer()
                if self._checkpointer_factory is None
                else self._checkpointer_factory()
            ),
        )

        return CompiledAgent(
            agent_type_id=agent_type_id,
            version_id=version_id,
            graph=graph,
            runtime_config=runtime_config,
        )

    def _select_tools(self, names: list[str], agent_type_id: str) -> list[Any]:
        """Subconjunto de tools por nombre; vacío → todas. Valida nombres."""
        if not names:
            return list(TOOLS_BY_NAME.values())

        selected = []
        for name in names:
            tool = TOOLS_BY_NAME.get(name)
            if tool is None:
                raise AgentRuntimeError(
                    f"Tool '{name}' desconocida para agente {agent_type_id}. "
                    f"Disponibles: {sorted(TOOLS_BY_NAME)}"
                )
            selected.append(tool)
        return selected


# Singleton del proceso: los endpoints internos invalidan y los de chat leen.
registry = AgentRuntimeRegistry()


def get_runtime_registry() -> AgentRuntimeRegistry:
    return registry
