from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ControlContext(BaseModel):
    """Contexto del control de programa activo enviado por el backend .NET.

    Solo está presente cuando el turno corresponde a un control de programa
    abierto (recordatorio de subir el examen de laboratorio del día del hito).
    Los cuatro campos son obligatorios; un contexto malformado se rechaza con
    422 (nunca 500).
    """

    send_id: str = Field(description="UUID del envío del control (app.program_controls.id).")
    milestone_day: int = Field(
        ge=1, description="Día del hito del programa al que pertenece el control."
    )
    status: str = Field(description="Estado del control: sent | responded | followed_up.")
    exam_pending: bool = Field(
        description="True si el examen de laboratorio sigue pendiente de subir."
    )


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
    control_context: ControlContext | None = Field(
        default=None,
        description=(
            "Contexto del control de programa activo (backend). Ausente o null ⇒ "
            "comportamiento byte-idéntico al chat estándar (sin guía ni señal)."
        ),
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
    control_signal: str | None = Field(
        default=None,
        description=(
            "Señal estructurada del control de programa (p. ej. 'declined'). "
            "Solo se emite cuando el paciente rechazó explícitamente subir su "
            "examen; ausente/null en cualquier otro caso."
        ),
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


class ThreadMessageResponse(BaseModel):
    """Mensaje individual del historial visible de una conversación."""

    role: str = Field(description="Rol del emisor: 'user' (paciente) o 'bot' (agente).")
    text: str = Field(description="Texto plano del mensaje (sin bloques internos de tool_use).")


class ThreadStateResponse(BaseModel):
    thread_id: str
    message_count: int
    last_message: str | None = None
    messages: list[ThreadMessageResponse] = Field(
        default_factory=list,
        description=(
            "Página solicitada del historial visible, en orden cronológico "
            "(más reciente al final; máximo `limit` mensajes, tope 100)."
        ),
    )
    has_more: bool = Field(
        default=False,
        description="True cuando existe historial visible más antiguo que esta página.",
    )
    next_cursor: int | None = Field(
        default=None,
        description=(
            "Valor a enviar como `before` para pedir la página anterior; "
            "null cuando `has_more` es false."
        ),
    )


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
    empathetic_message: str = Field(
        default="",
        description=(
            "Mensaje empático para el paciente (vacío si la narración no aplica o falló). "
            "El backend lo prefiere sobre `summary` cuando no está vacío."
        ),
    )


class PreviousMeasurement(BaseModel):
    """Evolución pre-computada por el backend para una métrica del catálogo.

    La aritmética (delta) y la dirección las calcula el backend; el LLM solo
    narra. Los campos opcionales solo tienen sentido cuando `direction` no es
    `first_record`.
    """

    direction: str = Field(description="worsened | improved | stable | changed | first_record")
    previous_value: float | None = None
    previous_unit: str | None = None
    previous_date: datetime | None = None
    delta: float | None = Field(default=None, description="current - previous (calculado en .NET)")
    current_value: float | None = None
    current_unit: str | None = None


class NarrateRequest(BaseModel):
    """Cuerpo del endpoint de narración empática (POST /chat/lab-exam/narrate)."""

    metrics: list[LabExamMetric] = Field(
        default_factory=list,
        description="Métricas del examen actual (las mismas que devolvió la extracción).",
    )
    previous_measurements: dict[str, PreviousMeasurement] = Field(
        default_factory=dict,
        description="Evolución por métrica: dirección, delta y valores pre-computados en .NET.",
    )
    language: str | None = Field(
        default=None,
        description="Código de idioma ('es' | 'en'); ausente, vacío o desconocido ⇒ 'es'.",
    )


class NarrateResponse(BaseModel):
    """Respuesta del endpoint de narración. Siempre 200; vacío ante cualquier fallo."""

    empathetic_message: str = Field(default="", description="Mensaje empático generado.")
