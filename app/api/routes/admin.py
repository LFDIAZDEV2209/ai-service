"""Endpoints de observabilidad/administración de ejecuciones de agentes.

Responden las 12 preguntas del plan de agentes (sección 13):
qué agente/versión/modelo respondió, qué documentos/memoria/tools usó,
latencia, tokens, errores, feedback, evaluaciones y experiencias.

Nota de seguridad: estos endpoints NO aplican autenticación propia — el
backend .NET (Auth Service) los expone tras su autorización por permisos
(`Agents.View`) y el servicio solo debe escuchar en la red interna en
producción.
"""

from __future__ import annotations

import logging
import uuid as uuid_lib
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.security import require_internal_key
from app.db.models import (
    AgentEvaluation,
    AgentExecution,
    AgentExperience,
    AgentFeedback,
)

logger = logging.getLogger(__name__)

# Canal interno: expone inputs/outputs completos, user_id y feedback de todas
# las ejecuciones. Solo el backend (con X-Internal-Key) lo invoca tras su
# propia autorización administrativa (permiso `Agents.View`).
router = APIRouter(
    prefix="/admin/executions",
    tags=["admin"],
    dependencies=[Depends(require_internal_key)],
)

# Ventana máxima de listado sin filtros (protege contra queries de rango completo).
_DEFAULT_WINDOW_DAYS = 7
_MAX_LIMIT = 100


class ExecutionSummary(BaseModel):
    """Resumen de una ejecución (listado)."""

    id: str
    thread_id: str | None = None
    agent_type_id: str
    version_id: str | None = None
    user_id: str | None = None
    status: str
    latency_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    model: str | None = None
    error: str | None = None
    created_at: datetime
    ended_at: datetime | None = None
    feedback_rating: int | None = None
    best_evaluation: float | None = None


class ExecutionDetail(ExecutionSummary):
    """Detalle completo de una ejecución (las 12 preguntas)."""

    input: dict | None = None
    output: dict | None = None
    feedback_comment: str | None = None
    evaluations: list[dict] = Field(default_factory=list)
    experiences: list[dict] = Field(default_factory=list)


class ExecutionListResponse(BaseModel):
    total: int
    items: list[ExecutionSummary]


async def _load_extra(execution: AgentExecution, session: AsyncSession) -> dict:
    """Carga feedback, evaluaciones y experiencias ligadas a la ejecución."""
    extra: dict = {"feedback_rating": None, "feedback_comment": None}

    feedbacks = (
        (
            await session.execute(
                select(AgentFeedback).where(AgentFeedback.thread_id == execution.thread_id)
            )
        )
        .scalars()
        .all()
    )
    if feedbacks:
        last = max(feedbacks, key=lambda f: f.created_at)
        extra["feedback_rating"] = last.rating
        extra["feedback_comment"] = last.comment

    evaluations = (
        (
            await session.execute(
                select(AgentEvaluation).where(AgentEvaluation.execution_id == execution.id)
            )
        )
        .scalars()
        .all()
    )
    extra["evaluations"] = [
        {
            "evaluator": e.evaluator,
            "metric": e.metric,
            "score": e.score,
            "details": e.details,
        }
        for e in evaluations
    ]
    extra["best_evaluation"] = max((e.score for e in evaluations), default=None)

    experiences = (
        (
            await session.execute(
                select(AgentExperience).where(
                    AgentExperience.agent_type_id == execution.agent_type_id
                )
            )
        )
        .scalars()
        .all()
    )
    extra["experiences"] = [
        {
            "trigger": e.trigger_pattern,
            "response": e.response_pattern,
            "outcome": e.outcome,
            "recurrence": e.recurrence_count,
            "rating": e.success_rating,
        }
        for e in experiences
    ]
    return extra


async def _execution_model(row: AgentExecution) -> str | None:
    """Modelo de la ejecución: input (si el agente lo configuró) o output
    (fallback al provider real que respondió)."""
    if row.input and row.input.get("model"):
        return row.input.get("model")
    if row.output and row.output.get("model"):
        return row.output.get("model")
    if row.output and row.output.get("provider"):
        return row.output.get("provider")
    return None


@router.get("", response_model=ExecutionListResponse)
async def list_executions(
    agent_type_id: str | None = None,
    user_id: str | None = None,
    status: str | None = Query(default=None, pattern="^(completado|error|ejecutando)$"),
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> ExecutionListResponse:
    """Lista ejecuciones con filtros opcionales (paginado).

    Por defecto solo devuelve los últimos 7 días (ventana de protección).
    """
    window = from_date or (datetime.now(UTC) - timedelta(days=_DEFAULT_WINDOW_DAYS))
    to = to_date or datetime.now(UTC)

    conditions = [
        AgentExecution.created_at >= window,
        AgentExecution.created_at <= to,
    ]
    if agent_type_id:
        conditions.append(AgentExecution.agent_type_id == agent_type_id)
    if user_id:
        conditions.append(AgentExecution.user_id == user_id)
    if status:
        conditions.append(AgentExecution.status == status)

    total = (
        await session.execute(select(func.count(AgentExecution.id)).where(*conditions))
    ).scalar_one()

    rows = (
        (
            await session.execute(
                select(AgentExecution)
                .where(*conditions)
                .order_by(AgentExecution.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    items: list[ExecutionSummary] = []
    for row in rows:
        extra = await _load_extra(row, session)
        items.append(
            ExecutionSummary(
                id=row.id,
                thread_id=row.thread_id,
                agent_type_id=row.agent_type_id,
                version_id=row.version_id,
                user_id=row.user_id,
                status=row.status,
                latency_ms=row.latency_ms,
                tokens_in=row.tokens_in,
                tokens_out=row.tokens_out,
                model=await _execution_model(row),
                error=row.error,
                created_at=row.created_at,
                ended_at=row.ended_at,
                feedback_rating=extra["feedback_rating"],
                best_evaluation=extra["best_evaluation"],
            )
        )

    return ExecutionListResponse(total=total, items=items)


@router.get("/{execution_id}", response_model=ExecutionDetail)
async def get_execution(
    execution_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> ExecutionDetail:
    """Detalle completo de una ejecución (las 12 preguntas del monitoreo)."""
    try:
        uuid_lib.UUID(execution_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Ejecución no encontrada") from None

    row = (
        await session.execute(select(AgentExecution).where(AgentExecution.id == execution_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Ejecución no encontrada")
    extra = await _load_extra(row, session)

    return ExecutionDetail(
        id=row.id,
        thread_id=row.thread_id,
        agent_type_id=row.agent_type_id,
        version_id=row.version_id,
        user_id=row.user_id,
        status=row.status,
        latency_ms=row.latency_ms,
        tokens_in=row.tokens_in,
        tokens_out=row.tokens_out,
        model=await _execution_model(row),
        error=row.error,
        created_at=row.created_at,
        ended_at=row.ended_at,
        input=row.input,
        output=row.output,
        feedback_rating=extra["feedback_rating"],
        feedback_comment=extra["feedback_comment"],
        evaluations=extra["evaluations"],
        experiences=extra["experiences"],
        best_evaluation=extra["best_evaluation"],
    )
