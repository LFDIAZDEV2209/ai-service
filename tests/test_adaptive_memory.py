"""Tests de la fase 6 — adaptive memory (feedback, evaluaciones, experiencias).

Cubren:
- Evaluación heurística post-turno (score determinista).
- Feedback → creación/refuerzo de experiencias (upsert por trigger).
- Inyección: solo experiencias exitosas y recurrentes, aisladas por agente.
- Separación de categorías: experiencia del agente ≠ memoria del usuario.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.memory.adaptive import (
    OUTCOME_ERROR,
    OUTCOME_SUCCESS,
    AdaptiveMemoryService,
)

AGENT_DOC = "agent-nutricion"
AGENT_PSY = "agent-psicologia"


class FakeExperience:
    def __init__(self, **kwargs):
        self.id = kwargs.pop("id", "exp-1")
        for k, v in kwargs.items():
            setattr(self, k, v)
        self.created_at = getattr(self, "created_at", datetime.now(UTC))
        self.last_used_at = getattr(self, "last_used_at", None)


class FakeResult:
    def __init__(self, existing=None, rows=None):
        self._existing = existing
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._existing

    def scalars(self):
        return SimpleNamespace(all=lambda: self._rows)


class FakeSession:
    """Sesión fake: el test controla qué devuelve cada `execute`."""

    def __init__(self):
        self.experiences: list[FakeExperience] = []
        self.feedback: list = []
        self.evaluations: list = []
        self._existing: FakeExperience | None = None
        self._rows: list[FakeExperience] = []

    def set_existing(self, exp: FakeExperience | None) -> None:
        self._existing = exp

    def set_rows(self, rows: list[FakeExperience]) -> None:
        self._rows = rows

    async def execute(self, stmt):
        return FakeResult(self._existing, self._rows)

    def add(self, obj) -> None:
        if hasattr(obj, "trigger_pattern"):
            self.experiences.append(obj)
        elif hasattr(obj, "rating"):
            self.feedback.append(obj)
        else:
            self.evaluations.append(obj)


def make_service(session: FakeSession) -> AdaptiveMemoryService:
    svc = AdaptiveMemoryService(session)  # type: ignore[arg-type]
    return svc


# ── Evaluación heurística ───────────────────────────────────────────────────


def test_evaluate_response_buena():
    svc = make_service(FakeSession())
    score, details = svc.evaluate_response(
        answer="Te recomiendo comer verduras de hoja verde y legumbres.",
        input_text="¿Qué me recomiendas para la diabetes?",
        tools_used=["retrieve_knowledge"],
    )
    assert score == 1.0
    assert details["respuesta_no_vacia"] is True
    assert details["uso_de_tools"] is True


def test_evaluate_response_vacia():
    svc = make_service(FakeSession())
    score, details = svc.evaluate_response(answer="   ", input_text="hola")
    assert score == 0.0
    assert details["respuesta_no_vacia"] is False


def test_evaluate_response_repite_prompt():
    svc = make_service(FakeSession())
    score, _ = svc.evaluate_response(answer="Hola", input_text="Hola")
    assert score < 1.0


# ── Feedback → experiencias ─────────────────────────────────────────────────


async def test_feedback_alto_crea_experiencia_exitosa():
    session = FakeSession()
    svc = make_service(session)

    exp = await svc.save_experience(
        agent_type_id=AGENT_DOC,
        version_id="v1",
        trigger="¿Qué puedo comer si tengo diabetes?",
        response="Prioriza alimentos de bajo índice glucémico.",
        rating=5,
    )

    assert exp.outcome == OUTCOME_SUCCESS
    assert exp.success_rating == 1.0
    assert exp.recurrence_count == 1


async def test_feedback_bajo_crea_experiencia_de_error():
    session = FakeSession()
    svc = make_service(session)

    exp = await svc.save_experience(
        agent_type_id=AGENT_DOC,
        version_id="v1",
        trigger="¿Qué es el IMC?",
        response="No sé qué es eso.",
        rating=1,
    )

    assert exp.outcome == OUTCOME_ERROR
    assert exp.success_rating == 0.2


async def test_feedback_repetido_refuerza_experiencia():
    """El mismo trigger varias veces incrementa recurrencia y promedia rating."""
    session = FakeSession()
    svc = make_service(session)

    for i, rating in enumerate((5, 5, 4)):
        if i > 0:
            # Simula el upsert: la BD ya tiene la experiencia con ese trigger.
            session.set_existing(session.experiences[0])
        await svc.save_experience(
            agent_type_id=AGENT_DOC,
            version_id="v1",
            trigger="¿Cómo calculo mis calorías diarias?",
            response="Usa la fórmula de Harris-Benedict.",
            rating=rating,
        )

    assert len(session.experiences) == 1
    exp = session.experiences[0]
    assert exp.recurrence_count == 3
    assert exp.outcome == OUTCOME_SUCCESS
    # Promedio móvil (pesa más lo reciente): 1.0 → (1.0+1.0)/2 → (1.0+0.8)/2
    assert abs(exp.success_rating - 0.9) < 1e-9


async def test_experiencia_sin_trigger_o_response_levanta_error():
    session = FakeSession()
    svc = make_service(session)

    try:
        await svc.save_experience(
            agent_type_id=AGENT_DOC,
            version_id=None,
            trigger="  ",
            response="respuesta",
            rating=5,
        )
        raise AssertionError("debería haber lanzado AdaptiveMemoryError")
    except Exception as exc:
        assert "obligatorios" in str(exc)


# ── Inyección: solo exitosas + recurrentes, aisladas ────────────────────────


def _seed_experiences(session: FakeSession) -> None:
    session.set_rows([
        FakeExperience(
            id="e1", agent_type_id=AGENT_DOC, trigger_pattern="t1",
            response_pattern="r1", outcome=OUTCOME_SUCCESS,
            success_rating=0.9, recurrence_count=5,
        ),
        FakeExperience(
            id="e2", agent_type_id=AGENT_DOC, trigger_pattern="t2",
            response_pattern="r2", outcome=OUTCOME_ERROR,
            success_rating=0.2, recurrence_count=3,
        ),
        FakeExperience(
            id="e3", agent_type_id=AGENT_DOC, trigger_pattern="t3",
            response_pattern="r3", outcome=OUTCOME_SUCCESS,
            success_rating=0.8, recurrence_count=1,  # sin recurrencia mínima
        ),
        FakeExperience(
            id="e4", agent_type_id=AGENT_PSY, trigger_pattern="t4",
            response_pattern="r4", outcome=OUTCOME_SUCCESS,
            success_rating=0.9, recurrence_count=9,
        ),
    ])


async def test_load_experiences_solo_exitosas_recurrentes_y_de_este_agente():
    session = FakeSession()
    _seed_experiences(session)
    svc = make_service(session)

    experiences = await svc.load_experiences(agent_type_id=AGENT_DOC)

    ids = [e.id for e in experiences]
    assert ids == ["e1"]  # e2=error, e3=recurrencia 1, e4=otro agente
    assert "e4" not in ids  # aislamiento entre agentes


async def test_format_experiences_bloque_separado():
    session = FakeSession()
    _seed_experiences(session)
    svc = make_service(session)

    experiences = await svc.load_experiences(agent_type_id=AGENT_DOC)
    text = svc.format_experiences(experiences)

    assert "Experiencia aprendida del agente" in text
    assert "r1" in text
    assert "t1" in text


def test_format_experiences_vacio():
    svc = make_service(FakeSession())
    assert svc.format_experiences([]) == ""


# ── Guardado de feedback ────────────────────────────────────────────────────


async def test_save_feedback_persiste():
    session = FakeSession()
    svc = make_service(session)

    fb = await svc.save_feedback(
        thread_id="thread-1", rating=4, comment="buena respuesta", user_id="u1"
    )

    assert fb.rating == 4
    assert len(session.feedback) == 1
