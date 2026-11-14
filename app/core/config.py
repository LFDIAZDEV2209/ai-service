"""Configuración central del servicio vía variables de entorno (pydantic-settings)."""

from functools import lru_cache

from pydantic import Field, field_validator, model_validator
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
    # Sampling global OMITIDO por defecto: los modelos thinking de Anthropic
    # rechazan el kwarg `temperature` ("Extra inputs are not permitted",
    # incidente 08-11: con el fallback 0.2 global, TODO chat de la app y de
    # los agentes runtime caía con 400). Definir LLM_TEMPERATURE solo si el
    # modelo del proveedor lo acepta; el nivel por agente va en su config
    # (agents.agent_type_versions.config.temperature).
    llm_temperature: float | None = None
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

    # Compatibilidad de configuración histórica: /chat/lab-exam/narrate ahora es
    # determinista y NO consume estos parámetros ni llama al proveedor.
    empathetic_provider: str = "openai"
    empathetic_model: str = "gpt-4o-mini"
    empathetic_temperature: float = 0.5
    empathetic_max_tokens: int = 400
    # Reservado para compatibilidad; sin efecto en la narración actual.
    empathetic_timeout: float = 20.0

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
    # URL del backend para consultas internas del contexto del paciente.
    backend_base_url: str = ""
    vita_runtime_enabled: bool = False
    vita_http_timeout: float = Field(default=3, gt=0, le=8)
    vita_turn_timeout: float = Field(default=20, gt=0, le=90)
    vita_voice_enabled: bool = False
    vita_transcript_sync_enabled: bool = False
    vita_transcript_sync_timeout: float = Field(default=5, gt=0, le=10)
    vita_voice_transport_verified: bool = False
    vita_elevenlabs_agent_id: str | None = None
    vita_elevenlabs_version_id: str | None = None
    vita_voice_service_key: str = ""
    vita_voice_context_secret: str = ""
    vita_voice_session_ttl: int = Field(default=600, ge=30, le=600)

    # CORS — allowlist explícita de orígenes (separados por coma en CORS_ORIGINS).
    # No hay clientes de navegador del AI Service hoy: el backend .NET lo consume
    # server-to-server. Se mantienen orígenes de desarrollo documentados.
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

    # Base de datos y migraciones
    database_url: str | None = None  # Postgres → checkpointer real; None → MemorySaver
    auto_migrate: bool = True  # Aplica migraciones Alembic automáticamente en startup
    memory_store_path: str = "./data/memory.json"

    # RAG
    docs_path: str = "./docs"
    rag_top_k: int = 5

    # ── ElevenLabs (voz conversacional) ────────────────────
    # Sesiones de voz para la app de pacientes. La API key vive SOLO en este
    # servicio (.env git-ignored / Secrets Manager en prod): nunca llega al
    # frontend ni se loguea. Cambiar de cuenta ElevenLabs = cambiar la key (y
    # el agent_id si el agente vive en otra cuenta).
    elevenlabs_api_key: str | None = None
    elevenlabs_agent_id: str | None = None
    # Residencia de datos: workspace global → https://api.elevenlabs.io;
    # workspace US → https://api.us.elevenlabs.io (el signed URL debe emitirse
    # contra la misma región que el agente, si no el OAuth/WS falla).
    elevenlabs_base_url: str = "https://api.elevenlabs.io"
    voice_enabled: bool = False
    # La verificación adicional de revisión se activa al retomar App Store.
    voice_review_verification_enabled: bool = False
    # Versiones publicadas e inmutables, una por idioma. Sin versión verificada
    # no se emite ninguna credencial de voz; no hay fallback al agente mutable.
    elevenlabs_patient_version_es: str | None = None
    elevenlabs_patient_version_en: str | None = None
    # JSON {tool_id: SHA256(tool_config canónico)} aprobado por el responsable.
    # No hay IDs implícitamente confiables ni herramientas de servidor permitidas.
    elevenlabs_patient_tool_config_sha256: dict[str, str] = Field(default_factory=dict)
    # Timeout de la llamada a ElevenLabs al emitir una sesión (signed URL).
    elevenlabs_timeout: float = 10.0

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
        if self.voice_enabled:
            if not self.elevenlabs_api_key:
                missing.append(
                    "ELEVENLABS_API_KEY (componente: app.voice.client — sesiones de voz)"
                )
            if not self.elevenlabs_agent_id:
                missing.append("ELEVENLABS_AGENT_ID (componente: app.voice.client — agente de voz)")

        if missing:
            raise ConfigError(
                "Configuración incompleta para entorno production. Faltan: " + "; ".join(missing)
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton de configuración (se cachea para no releer `.env` en cada request)."""
    return Settings()
