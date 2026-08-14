"""Tests de la fase 7 — observabilidad (execution tracker + fuentes RAG).

Cubren:
- Extracción de fuentes RAG del bloque `[FUENTES: ...]` de la tool.
- Registro de ejecuciones: start/complete/fail con métricas (tokens, latencia).
- Robustez: una ejecución fallida registra status=error sin romper el chat.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.observability.executions import (
    ExecutionTracker,
    extract_rag_sources,
)

EXEC_ID = "exec-1234"


class FakeExecution:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakeResult:
    def __init__(self, row=None):
        self._row = row

    async def scalar_one_or_none(self):
        return self._row


class FakeSession:
    def __init__(self):
        self.added: list = []
        self.fetched: dict[str, FakeExecution] = {}

    def add(self, obj) -> None:
        self.added.append(obj)
        if hasattr(obj, "id") and obj.id:
            self.fetched[obj.id] = obj

    async def get(self, model, execution_id: str):
        return self.fetched.get(execution_id)

    async def execute(self, stmt):
        return FakeResult()


def make_session() -> FakeSession:
    return FakeSession()


# ── Extracción de fuentes RAG ───────────────────────────────────────────────


def test_extract_rag_sources_parses_tool_message():
    messages = [
        ToolMessage(
            content=(
                "[Fuente: manual.md — Sección: Protocolo]\n"
                "texto del chunk\n\n"
                '[FUENTES: [{"source": "manual.md", "heading": "Protocolo", '
                '"score": 0.75}, {"source": "guia.md", "heading": "Dieta", '
                '"score": 0.5}]]'
            ),
            tool_call_id="call-1",
            name="retrieve_knowledge",
        )
    ]
    sources = extract_rag_sources(messages)
    assert len(sources) == 2
    assert sources[0]["source"] == "manual.md"
    assert sources[0]["score"] == 0.75


def test_extract_rag_sources_ignores_no_tool_messages():
    messages = [
        HumanMessage(content="hola"),
        AIMessage(content="respuesta"),
    ]
    assert extract_rag_sources(messages) == []


def test_extract_rag_sources_invalid_json_ignored():
    messages = [
        ToolMessage(
            content="[FUENTES: esto no es json]]",
            tool_call_id="call-1",
            name="retrieve_knowledge",
        )
    ]
    assert extract_rag_sources(messages) == []


# ── Tracker ─────────────────────────────────────────────────────────────────


async def test_tracker_start_crea_ejecucion():
    session = make_session()
    tracker = ExecutionTracker(session)  # type: ignore[arg-type]

    exec_id = await tracker.start(
        thread_id="thread-1",
        agent_type_id="agent-nutricion",
        version_id="v1",
        agent_instance_id="inst-1",
        user_id="user-1",
        provider="anthropic",
        model="claude-haiku",
        message="hola",
    )

    assert exec_id
    assert len(session.added) == 1
    row = session.added[0]
    assert row.status == "ejecutando"
    assert row.agent_type_id == "agent-nutricion"
    assert row.input["message"] == "hola"
    assert row.input["model"] == "claude-haiku"


async def test_tracker_complete_llena_metricas():
    session = make_session()
    tracker = ExecutionTracker(session)  # type: ignore[arg-type]
    exec_id = await tracker.start(
        thread_id="thread-1",
        agent_type_id="agent-nutricion",
        version_id="v1",
        agent_instance_id=None,
        user_id="user-1",
        provider="anthropic",
        model="claude-haiku",
        message="hola",
    )

    ai = AIMessage(
        content="respuesta final",
        usage_metadata={"input_tokens": 120, "output_tokens": 45, "total_tokens": 165},
    )
    state = {
        "messages": [HumanMessage(content="hola"), ai],
        "tools_used": ["retrieve_knowledge"],
        "provider": "FakeModel",
    }
    await tracker.complete(exec_id, result_state=state, model="claude-haiku", latency_ms=350)

    row = session.fetched[exec_id]
    assert row.status == "completado"
    assert row.tokens_in == 120
    assert row.tokens_out == 45
    assert row.latency_ms == 350
    assert row.output["answer"] == "respuesta final"
    assert row.output["tools_used"] == ["retrieve_knowledge"]
    assert row.output["model"] == "claude-haiku"
    assert row.ended_at is not None


async def test_tracker_complete_content_en_bloques():
    """Claude devuelve content como lista de bloques {type: text}; se extrae."""
    session = make_session()
    tracker = ExecutionTracker(session)  # type: ignore[arg-type]
    exec_id = await tracker.start(
        thread_id="thread-1",
        agent_type_id="base",
        version_id=None,
        agent_instance_id=None,
        user_id=None,
        provider=None,
        model=None,
        message="hola",
    )

    ai = AIMessage(
        content=[{"type": "text", "text": "Hola, "}, {"type": "text", "text": "buenos dias"}],
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )
    state = {"messages": [HumanMessage(content="hola"), ai], "provider": "ChatAnthropic"}
    await tracker.complete(exec_id, result_state=state, model=None, latency_ms=100)

    row = session.fetched[exec_id]
    assert row.output["answer"] == "Hola, buenos dias"
    assert row.output["model"] == "ChatAnthropic"  # fallback al provider


async def test_tracker_complete_con_fuentes_rag():
    session = make_session()
    tracker = ExecutionTracker(session)  # type: ignore[arg-type]
    exec_id = await tracker.start(
        thread_id="thread-1",
        agent_type_id="agent-nutricion",
        version_id=None,
        agent_instance_id=None,
        user_id=None,
        provider=None,
        model=None,
        message="que puedo comer?",
    )

    tool_msg = ToolMessage(
        content='[FUENTES: [{"source": "manual.md", "score": 0.8}]]',
        tool_call_id="call-1",
        name="retrieve_knowledge",
    )
    state = {
        "messages": [
            HumanMessage(content="que puedo comer?"),
            tool_msg,
            AIMessage(content="come verduras"),
        ],
        "tools_used": ["retrieve_knowledge"],
    }
    await tracker.complete(exec_id, result_state=state, model=None, latency_ms=200)

    row = session.fetched[exec_id]
    assert row.output["rag_sources"] == [{"source": "manual.md", "score": 0.8}]


async def test_tracker_fail_marca_error():
    session = make_session()
    tracker = ExecutionTracker(session)  # type: ignore[arg-type]
    exec_id = await tracker.start(
        thread_id="thread-1",
        agent_type_id="agent-nutricion",
        version_id=None,
        agent_instance_id=None,
        user_id=None,
        provider=None,
        model=None,
        message="hola",
    )

    await tracker.fail(exec_id, error="boom", latency_ms=100)

    row = session.fetched[exec_id]
    assert row.status == "error"
    assert row.error == "boom"
    assert row.latency_ms == 100


async def test_tracker_complete_sin_registro_no_falla():
    """Completar una ejecución inexistente no debe lanzar (best-effort)."""
    session = make_session()
    tracker = ExecutionTracker(session)  # type: ignore[arg-type]

    # No se llamó a start: no hay registro; complete no debe explotar.
    await tracker.complete(
        "no-existe",
        result_state={"messages": []},
        model=None,
        latency_ms=10,
    )
