"""Configuración de runtime de un agente (versionada en el backend).

Es la interpretación tipada del JSON `config` de `agents.agent_type_versions`
que el backend sincroniza en `ai.agent_runtime_configs`. Todas las claves son
opcionales con defaults sensatos: un agente mínimo solo necesita
`system_prompt`; el resto hereda del servicio o del perfil base.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RetrievalConfig(BaseModel):
    """Configuración de recuperación RAG para el agente."""

    enabled: bool = False
    knowledge_base_ids: list[str] = Field(default_factory=list)
    top_k: int = 5


class MemoryConfig(BaseModel):
    """Configuración de memoria de largo plazo del agente."""

    enabled: bool = False
    categories: list[str] = Field(default_factory=list)


class AgentRuntimeConfig(BaseModel):
    """Configuración ejecutable de un tipo de agente.

    - `tools`: subconjunto explícito; vacío hereda solo el conjunto legado congelado.
    - `provider`/`model`/`temperature`/`max_tokens`: overrides del LLM; si se
      omiten se usa la configuración global del servicio.
    """

    system_prompt: str = "Eres un asistente útil de CoppAddresd."
    prompt: str | None = None
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    tools: list[str] = Field(default_factory=list)
    retrieval_config: RetrievalConfig = Field(default_factory=RetrievalConfig)
    memory_config: MemoryConfig = Field(default_factory=MemoryConfig)
    max_tool_calls: int = Field(default=8, ge=0, le=16)
    tool_timeout_seconds: float = Field(default=3, gt=0, le=8)
    recursion_limit: int = Field(default=25, ge=3, le=50)

    def effective_system_prompt(self) -> str:
        """Devuelve el prompt de sistema a usar (el perfil gana si no hay propio)."""
        if self.prompt:
            return f"{self.system_prompt}\n\n{self.prompt}"
        return self.system_prompt
