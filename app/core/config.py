"""Configuración central del servicio vía variables de entorno (pydantic-settings)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Todas las variables de entorno del servicio, validadas al cargar."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "coppaddresd-ai-service"
    environment: str = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"

    # LLM — proveedor por defecto: "anthropic" | "openai"
    llm_provider: str = "anthropic"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 4096

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"

    openai_api_key: str | None = None
    openai_model: str = "gpt-4o"
    openai_embedding_model: str = "text-embedding-3-small"

    # Agente
    max_tool_calls: int = 8
    recursion_limit: int = 25

    # Seguridad
    max_input_length: int = 8000
    rate_limit_max_requests: int = 10
    rate_limit_window_seconds: int = 60

    # Clave compartida con el backend para endpoints internos (X-Internal-Key)
    internal_api_key: str = ""

    # Memoria
    database_url: str | None = None  # Postgres → checkpointer real; None → MemorySaver
    memory_store_path: str = "./data/memory.json"

    # RAG
    docs_path: str = "./docs"
    rag_top_k: int = 5

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton de configuración (se cachea para no releer `.env` en cada request)."""
    return Settings()
