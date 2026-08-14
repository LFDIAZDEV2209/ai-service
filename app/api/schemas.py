"""Esquemas Pydantic de la API."""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000, description="Mensaje del usuario")
    thread_id: str | None = Field(
        default=None,
        description="ID de conversación. Si se omite, se crea una nueva.",
    )
    agent: str = Field(default="base", description="Clave del perfil de agente")
    # Runtime multi-agente (ver app/agents/runtime_registry.py)
    agent_type_id: str | None = Field(
        default=None,
        description="ID del tipo de agente (backend). Si se omite, se usa el perfil `agent`.",
    )
    user_id: str | None = Field(
        default=None,
        description="ID del usuario (auth.users) para aislar memoria/estado.",
    )
    patient_id: str | None = Field(
        default=None,
        description="ID del paciente asociado (aisla contexto clínico).",
    )
    agent_instance_id: str | None = Field(
        default=None,
        description="ID de la instancia de agente asignada al paciente.",
    )


class ChatResponse(BaseModel):
    thread_id: str
    answer: str
    agent: str = "base"
    tools_used: list[str] = Field(default_factory=list)
    model: str | None = None


class HealthResponse(BaseModel):
    status: str
    environment: str
    provider: str
    model: str
    tools: list[str]
    version: str


class IngestRequest(BaseModel):
    path: str = Field(min_length=1, description="Directorio con documentos .md/.txt")
    chunk_size: int = Field(default=1500, ge=100, le=10_000)


class IngestResponse(BaseModel):
    files_scanned: int
    files_ingested: int
    chunks_created: int
    errors: list[str] = Field(default_factory=list)


class ThreadStateResponse(BaseModel):
    thread_id: str
    message_count: int
    last_message: str | None = None
