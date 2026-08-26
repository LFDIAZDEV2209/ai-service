"""Configuración central del servicio vía variables de entorno (pydantic-settings)."""

from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.errors import ConfigError


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
    # Timeout de la invocación completa del grafo (LLM + tools + RAG) en el
    # chat síncrono. El backend corta a los 120s; este límite es menor para
    # que el AI Service responda antes de que el backend asuma timeout.
    llm_invoke_timeout: float = 90

    # Generación de planes (wellness) — endpoint interno /internal/wellness/generate-plan.
    # Temperatura baja para salida determinista y estructurada (JSON).
    plan_generation_temperature: float = 0.3
    # Timeout de la invocación LLM para generar un plan (una sola llamada).
    plan_generation_timeout: float = 60
    # Presupuesto de tokens de salida para generar un plan. Un plan de 7 días
    # con 4 comidas/día y macros es un JSON largo: 4096 (default global) suele
    # quedarse corto y truncar la respuesta (stop_reason=max_tokens), lo que
    # rompe el structured output. Valor alto para no truncar.
    plan_generation_max_tokens: int = 8000

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
    # Límite por cliente identificado (user_id real del backend/JWT); se cae
    # a IP solo cuando no hay identidad. Suficiente para uso normal (un chat
    # con streaming no cuenta por token, una request por turno).
    rate_limit_max_requests: int = 60
    rate_limit_window_seconds: int = 60

    # Clave compartida con el backend para endpoints internos (X-Internal-Key)
    internal_api_key: str = ""

    # CORS — allowlist explícita de orígenes (separados por coma en CORS_ORIGINS).
    # No hay clientes de navegador del AI Service hoy: el backend .NET lo consume
    # server-to-server. Se mantienen orígenes de desarrollo documentados.
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

    # Memoria
    database_url: str | None = None  # Postgres → checkpointer real; None → MemorySaver
    memory_store_path: str = "./data/memory.json"

    # RAG
    docs_path: str = "./docs"
    rag_top_k: int = 5

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, v):
        """Acepta `CORS_ORIGINS` como lista JSON o string separado por comas."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    @model_validator(mode="after")
    def _validate_production(self) -> "Settings":
        """En producción las variables críticas son obligatorias; sin ellas la
        aplicación no arranca (error claro, sin exponer valores)."""
        if not self.is_production:
            return self

        missing: list[str] = []
        if not self.database_url:
            missing.append(
                "DATABASE_URL (componente: app.db.engine — conexión SQLAlchemy async, "
                "schema ai; y app.memory.checkpointer)"
            )
        if not self.internal_api_key:
            missing.append(
                "INTERNAL_API_KEY (componente: app.api.routes.internal — endpoints X-Internal-Key)"
            )
        provider = self.llm_provider.strip().lower()
        if provider == "anthropic" and not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY (componente: app.llm.factory — modelo de chat)")
        if provider == "openai" and not self.openai_api_key:
            missing.append("OPENAI_API_KEY (componente: app.llm.factory — modelo de chat)")

        if missing:
            raise ConfigError(
                "Configuración incompleta para entorno production. Faltan: " + "; ".join(missing)
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton de configuración (se cachea para no releer `.env` en cada request)."""
    return Settings()
