from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


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


class ChatSuggestion(BaseModel):
    """Sugerencia de acción estructurada para la capa de presentación.

    v1: solo CTA de agenda de cita ("Agenda tu cita aquí") que la app móvil
    del paciente puede renderizar como botón. El `reason` y la `urgency` los
    decide el agente al llamar la tool `suggest_appointment`.
    """

    type: str = "appointment"
    cta_text: str = "Agenda tu cita aquí"
    reason: str | None = None
    urgency: str = "normal"


class ChatResponse(BaseModel):
    thread_id: str
    answer: str
    agent: str = "base"
    tools_used: list[str] = Field(default_factory=list)
    model: str | None = None
    execution_id: str | None = Field(
        default=None,
        description="ID de la ejecución registrada (observabilidad); úsalo para enviar feedback.",
    )
    suggestions: list[ChatSuggestion] = Field(
        default_factory=list,
        description="Sugerencias de acción estructuradas (p. ej. CTA de cita).",
    )


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


class FeedbackRequest(BaseModel):
    """Feedback del usuario sobre una respuesta del agente.

    `agent_type_id` + `trigger` + `response` permiten crear/refinar una
    experiencia aprendida (adaptive memory) sin depender del checkpointer.
    """

    thread_id: str = Field(min_length=1, description="Conversación evaluada")
    rating: int = Field(ge=1, le=5, description="Puntuación 1-5")
    comment: str | None = Field(default=None, max_length=2000)
    user_id: str | None = None
    execution_id: str | None = Field(
        default=None,
        description="ID de la ejecución (devuelto por /chat) para vincular el feedback.",
    )
    agent_type_id: str | None = Field(
        default=None,
        description="Tipo de agente (backend). Si se omite, solo se persiste el feedback.",
    )
    trigger: str | None = Field(
        default=None,
        description="Mensaje del usuario que disparó la respuesta evaluada.",
    )
    response: str | None = Field(
        default=None,
        description="Respuesta del agente evaluada.",
    )


class FeedbackResponse(BaseModel):
    thread_id: str
    rating: int
    experience_saved: bool
    outcome: str | None = None


class LabExamMetric(BaseModel):
    """Métrica clínica individual extraída de un examen de laboratorio."""

    model_config = ConfigDict(populate_by_name=True)

    metric_name: str = Field(
        validation_alias=AliasChoices("metric_name", "metric_key"),
        description="Nombre canónico de la métrica en el catálogo.",
    )
    value: float = Field(description="Valor numérico de la medición.")
    unit_symbol: str = Field(
        default="",
        validation_alias=AliasChoices("unit_symbol", "unit"),
        description="Símbolo de la unidad de medida (ej: mg/dL, %, mmHg).",
    )
    observed_at: datetime | None = Field(
        default=None,
        description="Fecha y hora de observación si está presente en el documento.",
    )


class LabExamResponse(BaseModel):
    """Respuesta estructurada de la extracción de exámenes de laboratorio."""

    summary: str = Field(description="Resumen textual breve de las métricas detectadas.")
    metrics: list[LabExamMetric] = Field(
        default_factory=list,
        description="Lista de métricas del catálogo extraídas del documento.",
    )
    readable: bool = Field(
        default=True,
        description="Indica si el documento fue legible o contiene texto extraíble.",
    )
