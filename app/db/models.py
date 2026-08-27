"""Modelos SQLAlchemy del schema `ai` (AI Service).

Las tablas de dominio conviven en el mismo schema con las tablas internas del
checkpointer de LangGraph (`checkpoints`, `checkpoint_blobs`, ...). Los FKs
hacia el backend (`agents.*`, `auth."Users"`) se crean por SQL en la migración,
no en el modelo (los schemas `agents`/`auth` los gestiona otro servicio).
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# El backend indexa con la extensión vector (pgvector) en el mismo Postgres.
EMBEDDING_DIMENSIONS = 1536  # text-embedding-3-small

SCHEMA = "ai"


def _uuid() -> Mapped[str]:
    """Columna UUID nativa con default en la base (evita generarlo en la app)."""
    return mapped_column(
        Uuid(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _timestamptz(name: str, nullable: bool = False, default_now: bool = False) -> Mapped[datetime]:
    """Columna de timestamp con timezone."""
    return mapped_column(
        name, DateTime(timezone=True), nullable=nullable,
        server_default=text("now()") if default_now else None,
    )


class Thread(Base):
    """Conversación de un usuario con una instancia de agente."""

    __tablename__ = "threads"
    __table_args__ = (
        Index("ix_threads_agent_instance_id", "agent_instance_id"),
        Index("ix_threads_user_id", "user_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = _uuid()
    agent_type_id: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_instance_id: Mapped[str | None] = mapped_column(String(128))
    user_id: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="activa")
    extra: Mapped[dict | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = _timestamptz("created_at", default_now=True)
    updated_at: Mapped[datetime | None] = _timestamptz("updated_at", nullable=True)
    last_message_at: Mapped[datetime | None] = _timestamptz("last_message_at", nullable=True)


class Message(Base):
    """Mensaje de una conversación. El checkpointer ya persiste el historial;
    esta tabla guarda una vista de negocio (para consultas/dashboard)."""

    __tablename__ = "messages"
    __table_args__ = (
        ForeignKeyConstraint(["thread_id"], ["ai.threads.id"], ondelete="CASCADE"),
        Index("ix_messages_thread_id_created_at", "thread_id", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = _uuid()
    thread_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls: Mapped[dict | None] = mapped_column(JSON)
    tool_results: Mapped[dict | None] = mapped_column(JSON)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _timestamptz("created_at", default_now=True)


class KnowledgeChunk(Base):
    """Chunk de documento indexado con embedding vectorial (pgvector)."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index("ix_knowledge_chunks_knowledge_base_id", "knowledge_base_id"),
        Index("ix_knowledge_chunks_document_id", "document_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = _uuid()
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    knowledge_base_id: Mapped[str] = mapped_column(String(36), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
    extra: Mapped[dict | None] = mapped_column("metadata", JSON)
    chunk_index: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _timestamptz("created_at", default_now=True)


class AgentMemory(Base):
    """Memoria de largo plazo de un usuario/instancia (perfil, preferencias,
    hechos clínicos confirmados). Categorizada y con relevancia."""

    __tablename__ = "agent_memories"
    __table_args__ = (
        Index("ix_agent_memories_agent_instance_id", "agent_instance_id"),
        Index("ix_agent_memories_user_id_agent_type_id", "user_id", "agent_type_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = _uuid()
    agent_instance_id: Mapped[str | None] = mapped_column(String(128))
    user_id: Mapped[str | None] = mapped_column(String(128))
    agent_type_id: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="chat")
    created_at: Mapped[datetime] = _timestamptz("created_at", default_now=True)
    updated_at: Mapped[datetime | None] = _timestamptz("updated_at", nullable=True)
    last_accessed_at: Mapped[datetime | None] = _timestamptz("last_accessed_at", nullable=True)


class AgentExperience(Base):
    """Memoria adaptativa: patrones de interacción que se repiten con éxito.
    Permite acelerar respuestas conocidas sin re-razonar cada vez."""

    __tablename__ = "agent_experiences"
    __table_args__ = (
        Index("ix_agent_experiences_agent_type_id", "agent_type_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = _uuid()
    agent_type_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version_id: Mapped[str | None] = mapped_column(String(128))
    trigger_pattern: Mapped[str] = mapped_column(Text, nullable=False)
    response_pattern: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    success_rating: Mapped[float] = mapped_column(Float, default=0.5)
    recurrence_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = _timestamptz("last_used_at", nullable=True)
    created_at: Mapped[datetime] = _timestamptz("created_at", default_now=True)


class AgentFeedback(Base):
    """Feedback explícito del usuario sobre una ejecución/respuesta."""

    __tablename__ = "agent_feedback"
    __table_args__ = (
        Index("ix_agent_feedback_execution_id", "execution_id"),
        Index("ix_agent_feedback_created_at", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = _uuid()
    execution_id: Mapped[str | None] = mapped_column(String(64))
    thread_id: Mapped[str | None] = mapped_column(String(128))
    user_id: Mapped[str | None] = mapped_column(String(128))
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _timestamptz("created_at", default_now=True)


class AgentEvaluation(Base):
    """Evaluación programática de una ejecución (heurística o LLM-as-judge)."""

    __tablename__ = "agent_evaluations"
    __table_args__ = (
        Index("ix_agent_evaluations_execution_id", "execution_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = _uuid()
    execution_id: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluator: Mapped[str] = mapped_column(String(20), nullable=False)
    metric: Mapped[str] = mapped_column(String(50), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = _timestamptz("created_at", default_now=True)


class AgentExecution(Base):
    """Registro de una ejecución del grafo (observabilidad, costos, métricas)."""

    __tablename__ = "agent_executions"
    __table_args__ = (
        Index("ix_agent_executions_thread_id", "thread_id"),
        Index("ix_agent_executions_agent_type_id_created_at", "agent_type_id", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = _uuid()
    thread_id: Mapped[str | None] = mapped_column(String(128))
    agent_type_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version_id: Mapped[str | None] = mapped_column(String(128))
    agent_instance_id: Mapped[str | None] = mapped_column(String(128))
    user_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    input: Mapped[dict | None] = mapped_column(JSON)
    output: Mapped[dict | None] = mapped_column(JSON)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _timestamptz("created_at", default_now=True)
    ended_at: Mapped[datetime | None] = _timestamptz("ended_at", nullable=True)


class AgentRuntimeConfig(Base):
    """Configuración de runtime cacheada en el AI Service para un tipo de agente.
    Refleja la versión activa del backend (sincronizada vía endpoint interno)."""

    __tablename__ = "agent_runtime_configs"
    __table_args__ = (
        UniqueConstraint("agent_type_id", name="uq_agent_runtime_configs_agent_type_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = _uuid()
    agent_type_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version_id: Mapped[str | None] = mapped_column(String(36))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, nullable=False)
    synced_at: Mapped[datetime | None] = _timestamptz("synced_at", nullable=True)
    created_at: Mapped[datetime] = _timestamptz("created_at", default_now=True)
    updated_at: Mapped[datetime | None] = _timestamptz("updated_at", nullable=True)
