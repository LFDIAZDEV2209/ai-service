"""Tests de la fase 5 — memoria de largo plazo del usuario.

Cubren:
- Extracción heurística de hechos (sin LLM, determinista).
- Aislamiento estricto: dos usuarios/agentes nunca ven las memorias del otro.
- Persistencia/deduplicación y resumen rodante (service con store fake).
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.memory.extract import (
    CATEGORY_CLINICO,
    CATEGORY_OBJETIVO,
    CATEGORY_PERSONAL,
    CATEGORY_PREFERENCIA,
    extract_facts,
)
from app.memory.service import UserMemoryService

USER_A = "user-aaaa"
USER_B = "user-bbbb"
AGENT_DOC = "agent-nutricion"
AGENT_PSY = "agent-psicologia"


# ── Extracción heurística ───────────────────────────────────────────────────


def test_extract_facts_nombre_y_edad():
    result = extract_facts("Me llamo Ana y tengo 35 años.")
    categories = {f.category for f in result.facts}
    assert CATEGORY_PERSONAL in categories
    contents = {f.content for f in result.facts}
    assert any("Ana" in c for c in contents)
    assert any("35" in c for c in contents)


def test_extract_facts_preferencia_y_objetivo():
    result = extract_facts(
        "Prefiero comida vegetariana. Mi objetivo es bajar 10 kilos."
    )
    categories = {f.category for f in result.facts}
    assert CATEGORY_PREFERENCIA in categories
    assert CATEGORY_OBJETIVO in categories


def test_extract_facts_clinico_alergia():
    result = extract_facts("Soy alérgico a la penicilina.")
    assert any(f.category == CATEGORY_CLINICO for f in result.facts)


def test_extract_facts_alergia_sin_tilde():
    """Los usuarios suelen escribir sin tildes; el patrón debe tolerarlo."""
    result = extract_facts("soy alergica a los frutos secos")
    assert any(f.category == CATEGORY_CLINICO for f in result.facts)
    assert any("frutos secos" in f.content for f in result.facts)


def test_extract_facts_sin_hechos():
    result = extract_facts("Hola, ¿cómo estás?")
    assert not result.has_facts


def test_extract_facts_deduplica():
    result = extract_facts("Me llamo Ana. Me llamo Ana otra vez.")
    assert sum(1 for f in result.facts if "Ana" in f.content) == 1


# ── Store fake (sin BD) ─────────────────────────────────────────────────────


class FakeMemory:
    def __init__(self, **kwargs):
        self.id = kwargs.pop("id", "m1")
        for k, v in kwargs.items():
            setattr(self, k, v)
        self.created_at = getattr(self, "created_at", datetime.now(UTC))
        self.updated_at = getattr(self, "updated_at", None)
        self.last_accessed_at = getattr(self, "last_accessed_at", None)


class FakeSession:
    """Sesión fake con filas en memoria (filtros mínimos)."""

    def __init__(self, rows: list[FakeMemory] | None = None):
        self.rows = list(rows or [])
        self.added: list[FakeMemory] = []

    def add(self, obj) -> None:
        self.added.append(obj)
        self.rows.append(obj)


class FakeStore:
    def __init__(self, session: FakeSession):
        self.session = session
        self.saved: list[dict] = []

    async def list_memories(self, *, user_id, agent_type_id, agent_instance_id=None,
                            categories=None, limit=15) -> list[FakeMemory]:
        rows = [
            r for r in self.session.rows
            if r.user_id == user_id and r.agent_type_id == agent_type_id
            and (agent_instance_id is None or r.agent_instance_id == agent_instance_id)
        ]
        if categories:
            rows = [r for r in rows if r.category in categories]
        return sorted(rows, key=lambda r: r.importance, reverse=True)[:limit]

    async def save_memory(self, *, user_id, agent_type_id, agent_instance_id,
                          category, content, importance=0.5, source="chat",
                          dedupe_key=None):
        existing = None
        if dedupe_key is not None:
            existing = next(
                (r for r in self.session.rows
                 if r.user_id == user_id and r.agent_type_id == agent_type_id
                 and r.content == dedupe_key),
                None,
            )
        if existing is not None:
            existing.content = content
            return existing
        mem = FakeMemory(
            user_id=user_id, agent_type_id=agent_type_id,
            agent_instance_id=agent_instance_id, category=category,
            content=content, importance=importance, source=source,
        )
        self.session.add(mem)
        self.saved.append(mem.__dict__)
        return mem

    async def touch_memory(self, memory_id: str) -> None:
        pass

    async def delete_memories(self, *, user_id, agent_type_id, agent_instance_id=None) -> int:
        return 0


class FakeSummarizer:
    def __init__(self, text="resumen generado"):
        self.text = text
        self.calls = 0

    async def __call__(self, history, last_summary):
        self.calls += 1
        return self.text


def make_service(session: FakeSession, summarizer=None) -> UserMemoryService:
    store = FakeStore(session)
    svc = UserMemoryService(session)
    svc._store = store
    svc._summarizer = summarizer
    return svc


# ── Service: aislamiento estricto ───────────────────────────────────────────


async def test_aislamiento_entre_usuarios():
    """La memoria de un usuario nunca aparece en el contexto de otro."""
    session = FakeSession([
        FakeMemory(user_id=USER_A, agent_type_id=AGENT_DOC, category="personal",
                   content="Me llamo Ana", importance=0.9),
        FakeMemory(user_id=USER_B, agent_type_id=AGENT_DOC, category="clinico",
                   content="Soy alergico a penicilina", importance=0.9),
    ])
    svc = make_service(session)

    ctx_a = await svc.load_context(user_id=USER_A, agent_type_id=AGENT_DOC)
    ctx_b = await svc.load_context(user_id=USER_B, agent_type_id=AGENT_DOC)

    assert "Ana" in ctx_a
    assert "penicilina" not in ctx_a  # el usuario A NO ve la memoria de B
    assert "penicilina" in ctx_b
    assert "Ana" not in ctx_b


async def test_aislamiento_entre_agentes_del_mismo_usuario():
    """Las memorias de un agente no se inyectan en otro agente."""
    session = FakeSession([
        FakeMemory(user_id=USER_A, agent_type_id=AGENT_PSY, category="personal",
                   content="Tengo ansiedad", importance=0.9),
    ])
    svc = make_service(session)

    ctx_psy = await svc.load_context(user_id=USER_A, agent_type_id=AGENT_PSY)
    ctx_doc = await svc.load_context(user_id=USER_A, agent_type_id=AGENT_DOC)

    assert "ansiedad" in ctx_psy
    assert ctx_doc == ""  # el agente de nutrición no ve memorias del de psicología


async def test_load_context_sin_memorias_devuelve_vacio():
    svc = make_service(FakeSession())
    assert await svc.load_context(user_id=USER_A, agent_type_id=AGENT_DOC) == ""


async def test_load_context_respeta_categorias():
    session = FakeSession([
        FakeMemory(user_id=USER_A, agent_type_id=AGENT_DOC, category="personal",
                   content="Me llamo Ana", importance=0.9),
        FakeMemory(user_id=USER_A, agent_type_id=AGENT_DOC, category="resumen",
                   content="resumen de conversacion", importance=0.6),
    ])
    svc = make_service(session)
    ctx = await svc.load_context(user_id=USER_A, agent_type_id=AGENT_DOC,
                                 categories=["resumen"])
    assert "resumen" in ctx
    assert "Ana" not in ctx


# ── Service: escritura ──────────────────────────────────────────────────────


async def test_extract_and_save_persiste_hechos():
    session = FakeSession()
    svc = make_service(session)

    saved = await svc.extract_and_save(
        user_id=USER_A, agent_type_id=AGENT_DOC, agent_instance_id=None,
        message="Me llamo Ana y prefiero comida vegetariana.",
    )

    assert len(saved) == 2
    contents = {f.content for f in saved}
    assert any("Ana" in c for c in contents)
    assert any("comida vegetariana" in c for c in contents)
    # Los hechos quedaron en la sesión (pendientes de commit del llamador).
    assert len(session.rows) == 2


async def test_extract_and_save_sin_hechos_no_guarda():
    session = FakeSession()
    svc = make_service(session)

    saved = await svc.extract_and_save(
        user_id=USER_A, agent_type_id=AGENT_DOC, agent_instance_id=None,
        message="Hola, ¿cómo estás?",
    )

    assert saved == []
    assert session.rows == []


async def test_save_deduplica_por_clave():
    """Guardar el mismo hecho dos veces no duplica la memoria."""
    session = FakeSession()
    svc = make_service(session)

    await svc.extract_and_save(
        user_id=USER_A, agent_type_id=AGENT_DOC, agent_instance_id=None,
        message="Me llamo Ana.",
    )
    await svc.extract_and_save(
        user_id=USER_A, agent_type_id=AGENT_DOC, agent_instance_id=None,
        message="Me llamo Ana otra vez.",
    )

    anas = [r for r in session.rows if "Ana" in r.content]
    assert len(anas) == 1


# ── Resumen rodante ─────────────────────────────────────────────────────────


async def test_resumen_rodante_con_historico_corto_no_rota():
    session = FakeSession()
    svc = make_service(session, summarizer=FakeSummarizer())

    result = await svc.maybe_roll_summary(
        user_id=USER_A, agent_type_id=AGENT_DOC, agent_instance_id=None,
        history=["hola", "hola que tal"],
    )

    assert result is None
    assert session.rows == []


async def test_resumen_rodante_con_historico_largo_rota():
    session = FakeSession()
    summarizer = FakeSummarizer(text="resumen del historial")
    svc = make_service(session, summarizer=summarizer)

    history = [f"mensaje {i}" for i in range(8)]
    result = await svc.maybe_roll_summary(
        user_id=USER_A, agent_type_id=AGENT_DOC, agent_instance_id=None,
        history=history,
    )

    assert result == "resumen del historial"
    assert summarizer.calls == 1
    assert len(session.rows) == 1
    assert session.rows[0].category == "resumen"


async def test_resumen_rodante_fallback_determinista_sin_llm():
    session = FakeSession()
    svc = make_service(session)  # sin summarizer → fallback

    history = [f"mensaje {i}" for i in range(8)]
    result = await svc.maybe_roll_summary(
        user_id=USER_A, agent_type_id=AGENT_DOC, agent_instance_id=None,
        history=history,
    )

    assert result is not None
    assert "mensaje" in result


async def test_resumen_rodante_aislado_por_usuario():
    """El resumen del usuario A no afecta la consulta del usuario B."""
    session = FakeSession()
    svc = make_service(session)

    history = [f"m{i}" for i in range(8)]
    await svc.maybe_roll_summary(
        user_id=USER_A, agent_type_id=AGENT_DOC, agent_instance_id=None,
        history=history,
    )

    assert not await svc.get_summary(user_id=USER_B, agent_type_id=AGENT_DOC)
    assert await svc.get_summary(user_id=USER_A, agent_type_id=AGENT_DOC)
