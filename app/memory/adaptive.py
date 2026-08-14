"""Adaptive memory: aprendizaje del agente a partir de la experiencia de uso.

Mantiene SEPARADAS las cinco categorías del plan de arquitectura (nunca se
mezclan en una sola tabla):

- Conocimiento oficial → `ai.knowledge_chunks` (RAG, fase 4)
- Memoria personalizada → `ai.agent_memories` (fase 5)
- **Experiencia aprendida → `ai.agent_experiences` (este módulo)**
- Datos del paciente → tools de datos (backend, fase 3)
- Historial conversacional → checkpointer + `ai.messages`

Flujo:
1. Post-turno: `evaluate_response` puntúa la respuesta con heurísticas
   (no vacía, longitud, uso de tools) y persiste en `ai.agent_evaluations`.
2. Feedback del usuario (`POST /chat/feedback`): `save_feedback` persiste el
   rating y `save_experience` crea/refuerza un patrón de experiencia
   (trigger → response) con outcome success/error según el rating.
3. Inyección: `load_experiences` devuelve solo patrones exitosos y recurrentes
   del agente, formateados como bloque de contexto separado.

Reglas:
- La experiencia pertenece al AGENTE (agent_type_id), no al usuario: es
  conocimiento aprendido de uso agregado, aislada entre tipos de agente.
- Solo experiencias con `success_rating` alto y recurrencia se inyectan
  (evita ruido de un solo caso).
- Nunca se expone la experiencia de un agente a otro.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentEvaluation, AgentExperience, AgentFeedback

logger = logging.getLogger(__name__)

# Umbral de rating para considerar una experiencia como éxito.
_RATING_SUCCESS = 4
# Solo se inyectan experiencias con al menos esta recurrencia.
_MIN_RECURRENCE_TO_INJECT = 2
# Tamaños máximos de trigger/response almacenados.
_MAX_TRIGGER_CHARS = 200
_MAX_RESPONSE_CHARS = 1000
# Máximo de experiencias inyectadas al contexto.
_MAX_CONTEXT_EXPERIENCES = 5

OUTCOME_SUCCESS = "success"
OUTCOME_ERROR = "error"


def _rank_experiences(
    experiences: list[AgentExperience],
    *,
    agent_type_id: str,
    limit: int = _MAX_CONTEXT_EXPERIENCES,
    min_recurrence: int = _MIN_RECURRENCE_TO_INJECT,
) -> list[AgentExperience]:
    """Ranking puro de experiencias inyectables (defensa en profundidad).

    Filtra por agente, `outcome=success` y recurrencia mínima; ordena por
    (recurrencia x rating) descendente. Reutilizado por `load_experiences`
    y testeable sin BD.
    """
    eligible = [
        e
        for e in experiences
        if e.agent_type_id == agent_type_id
        and e.outcome == OUTCOME_SUCCESS
        and e.recurrence_count >= min_recurrence
    ]
    eligible.sort(
        key=lambda e: (
            e.recurrence_count * e.success_rating,
            e.last_used_at.timestamp() if e.last_used_at else 0,
        ),
        reverse=True,
    )
    return eligible[:limit]


class AdaptiveMemoryError(Exception):
    """Error de persistencia de adaptive memory."""


class AdaptiveMemoryService:
    """Orquesta feedback, evaluaciones y experiencias (sesión SQLAlchemy corta)."""

    def __init__(self, session: AsyncSession):
        self._session = session

    # ── Evaluación heurística post-turno ────────────────────────────────────

    def evaluate_response(
        self,
        *,
        answer: str,
        input_text: str,
        tools_used: list[str] | None = None,
    ) -> tuple[float, dict]:
        """Puntúa una respuesta con heurísticas deterministas (0..1).

        Métricas: respuesta no vacía, no replica el prompt, longitud razonable,
        usó herramientas si estaban disponibles. Devuelve (score, detalles).
        """
        tools_used = tools_used or []
        score = 0.0
        details: dict[str, bool] = {}

        non_empty = bool(answer and answer.strip())
        details["respuesta_no_vacia"] = non_empty
        if non_empty:
            score += 0.4

        not_repeat = non_empty and answer.strip().lower() != input_text.strip().lower()
        details["respuesta_diferente_al_prompt"] = not_repeat
        if not_repeat:
            score += 0.2

        reasonable_len = 20 <= len(answer.strip()) <= 4000
        details["longitud_razonable"] = reasonable_len
        if reasonable_len:
            score += 0.2

        if tools_used:
            details["uso_de_tools"] = True
            score += 0.2
        else:
            details["uso_de_tools"] = False

        return min(score, 1.0), details

    async def save_evaluation(
        self,
        *,
        execution_id: str,
        score: float,
        details: dict | None = None,
        metric: str = "quality",
        evaluator: str = "heuristic",
    ) -> AgentEvaluation:
        """Persiste una evaluación programática de una ejecución."""
        evaluation = AgentEvaluation(
            execution_id=execution_id,
            evaluator=evaluator,
            metric=metric,
            score=score,
            details=details,
        )
        self._session.add(evaluation)
        return evaluation

    # ── Feedback del usuario ────────────────────────────────────────────────

    async def save_feedback(
        self,
        *,
        thread_id: str,
        rating: int,
        comment: str | None = None,
        user_id: str | None = None,
        execution_id: str | None = None,
    ) -> AgentFeedback:
        """Persiste el feedback explícito del usuario sobre una respuesta.

        `execution_id` (si viene) vincula el feedback a la ejecución concreta
        del grafo, lo que permite a la observabilidad responder "¿el usuario
        dio feedback positivo o negativo?" por ejecución.
        """
        feedback = AgentFeedback(
            execution_id=execution_id,
            thread_id=thread_id,
            user_id=user_id,
            rating=rating,
            comment=comment,
        )
        self._session.add(feedback)
        logger.info(
            "Feedback %d/5 registrado (thread %s, execution %s, user %s)",
            rating,
            thread_id,
            execution_id,
            user_id,
        )
        return feedback

    # ── Experiencias ───────────────────────────────────────────────────────

    async def save_experience(
        self,
        *,
        agent_type_id: str,
        version_id: str | None,
        trigger: str,
        response: str,
        rating: int,
    ) -> AgentExperience:
        """Crea o refuerza un patrón de experiencia a partir de un feedback.

        Upsert por `trigger` normalizado: si el mismo disparador ya existe,
        incrementa `recurrence_count` y promedia `success_rating`. El outcome
        es success/error según el rating (>=4 → éxito).
        """
        trigger = trigger.strip()[: _MAX_TRIGGER_CHARS]
        response = response.strip()[: _MAX_RESPONSE_CHARS]
        if not trigger or not response:
            raise AdaptiveMemoryError("Trigger y response son obligatorios para la experiencia.")

        outcome = OUTCOME_SUCCESS if rating >= _RATING_SUCCESS else OUTCOME_ERROR
        new_rating = rating / 5.0

        existing = (
            await self._session.execute(
                select(AgentExperience).where(
                    AgentExperience.agent_type_id == agent_type_id,
                    AgentExperience.trigger_pattern == trigger,
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.response_pattern = response
            existing.outcome = outcome
            existing.success_rating = (existing.success_rating + new_rating) / 2
            existing.recurrence_count += 1
            existing.last_used_at = datetime.now(UTC)
            return existing

        experience = AgentExperience(
            agent_type_id=agent_type_id,
            version_id=version_id,
            trigger_pattern=trigger,
            response_pattern=response,
            outcome=outcome,
            success_rating=new_rating,
            recurrence_count=1,
            last_used_at=datetime.now(UTC),
        )
        self._session.add(experience)
        logger.info(
            "Experiencia %s guardada para agente %s (trigger %.50s)",
            outcome,
            agent_type_id,
            trigger,
        )
        return experience

    async def load_experiences(
        self,
        *,
        agent_type_id: str,
        limit: int = _MAX_CONTEXT_EXPERIENCES,
        min_recurrence: int = _MIN_RECURRENCE_TO_INJECT,
    ) -> list[AgentExperience]:
        """Experiencias exitosas y recurrentes del agente (para inyección).

        El SQL filtra por agente/outcome/recurrencia; `_rank_experiences`
        re-aplica el ranking como defensa en profundidad y para testabilidad.
        """
        result = await self._session.execute(
            select(AgentExperience)
            .where(
                AgentExperience.agent_type_id == agent_type_id,
                AgentExperience.outcome == OUTCOME_SUCCESS,
                AgentExperience.recurrence_count >= min_recurrence,
            )
            .order_by(
                (
                    AgentExperience.recurrence_count * AgentExperience.success_rating
                ).desc(),
                AgentExperience.last_used_at.desc().nullslast(),
            )
            .limit(limit)
        )
        return _rank_experiences(
            list(result.scalars().all()),
            agent_type_id=agent_type_id,
            limit=limit,
            min_recurrence=min_recurrence,
        )

    async def record_experience_use(self, experience_id: str) -> None:
        """Actualiza `last_used_at` al inyectar una experiencia al contexto."""
        exp = await self._session.get(AgentExperience, experience_id)
        if exp is not None:
            exp.last_used_at = datetime.now(UTC)

    # ── Formato para inyección ──────────────────────────────────────────────

    def format_experiences(self, experiences: list[AgentExperience]) -> str:
        """Bloque de contexto separado con los patrones aprendidos del agente."""
        if not experiences:
            return ""
        blocks = [
            f"- Disparador: {e.trigger_pattern}\n  Respuesta efectiva: {e.response_pattern}"
            for e in experiences
        ]
        return "Experiencia aprendida del agente (patrones que funcionaron):\n" + "\n".join(
            blocks
        )
