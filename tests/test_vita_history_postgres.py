"""Contrato real: saver PostgreSQL, SQLAlchemy y advisory locks de conexiones distintas.

Solo VITA_TEST_DATABASE_URL local con nombre *test*. Cada caso usa schema único;
la migración usa la BD descartable del wrapper, nunca bases del producto.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from alembic.config import Config
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.api.deps import get_db_session, get_graph
from app.api.main import create_app
from app.api.routes import chat, threads, vita_voice
from app.core.config import get_settings
from app.db.models import Message, Thread
from app.graph.graph import build_graph
from app.history import service
from app.history.store import VitaHistory
from app.memory.checkpointer import get_checkpointer
from app.vita import locking
from app.voice import store, transcript, vita_session
from app.voice.context import sign_context
from tests.fakes import RecordingFakeModel
from tests.test_api import FakeDbSession


def isolated_test_url():
    url = os.getenv("VITA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Falta VITA_TEST_DATABASE_URL aislada; no se toca BD compartida")
    parsed = make_url(url)
    if parsed.host not in {"127.0.0.1", "localhost"} or "test" not in (parsed.database or ""):
        pytest.fail("Se requiere PostgreSQL local aislado con nombre test")
    return url


@pytest.fixture
async def rig(monkeypatch):
    url = isolated_test_url()
    schema = f"vita_history_{uuid.uuid4().hex}"
    engine = create_async_engine(url, execution_options={"schema_translate_map": {"ai": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = [datetime(2026, 11, 30, 23, tzinfo=UTC)]
    history = service.HistoryService(sessions, clock=lambda: now[0])
    for module in (service, threads, chat, transcript, vita_session):
        monkeypatch.setattr(module, "history_service", history)
    monkeypatch.setattr(store, "async_session", sessions)
    monkeypatch.setattr(locking, "engine", engine)
    settings = get_settings()
    monkeypatch.setattr(settings, "vita_runtime_enabled", False)
    monkeypatch.setattr(settings, "vita_transcript_sync_enabled", True)
    monkeypatch.setattr(settings, "auto_migrate", False)
    connection = None
    try:
        async with engine.begin() as sql:
            await sql.execute(text(f'CREATE SCHEMA "{schema}"'))
            await sql.run_sync(
                lambda sync: VitaHistory.metadata.create_all(
                    sync,
                    tables=[
                        VitaHistory.__table__,
                        store.VitaVoiceSession.__table__,
                        store.VitaVoiceTurn.__table__,
                        Thread.__table__,
                        Message.__table__,
                    ],
                )
            )
        dsn = make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
        connection = await AsyncConnection.connect(
            dsn,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
            options=f"-c search_path={schema},public",
        )
        saver = AsyncPostgresSaver(connection)
        await saver.setup()
        monkeypatch.setattr(vita_session, "get_checkpointer", AsyncMock(return_value=saver))
        model = RecordingFakeModel(
            responses=[AIMessage(content="fixture response") for _ in range(20)]
        )
        graph = build_graph(model=model, checkpointer=saver)
        app = create_app()
        app.dependency_overrides[get_graph] = lambda: graph
        app.dependency_overrides[get_checkpointer] = lambda: saver

        class Session(FakeDbSession):
            def add(self, *args, **kwargs):
                pass

        app.dependency_overrides[get_db_session] = Session
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://ai.test",
            headers={"X-Internal-Key": settings.internal_api_key},
        ) as client:
            yield SimpleNamespace(
                engine=engine,
                sessions=sessions,
                history=history,
                saver=saver,
                graph=graph,
                model=model,
                client=client,
                now=now,
                schema=schema,
                monkeypatch=monkeypatch,
            )
    finally:
        if connection:
            await connection.close()
        async with engine.begin() as sql:
            await sql.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


def config(user="owner", thread="thread"):
    return {"configurable": {"thread_id": f"{user}::{thread}", "user_id": user}}


async def seed(rig, user="owner", thread="thread", *, opt_in=True):
    cfg = config(user, thread)
    if opt_in:
        await rig.history.prepare_turn(user, thread, rig.saver, cfg)
    await rig.graph.aupdate_state(
        cfg,
        {
            "messages": [
                HumanMessage(content="synthetic fixture"),
                AIMessage(content="old response"),
            ],
            "memory_context": "old summary",
            "agent": "base",
            "input": "old input",
        },
    )
    return cfg


async def voice(rig, *, user="owner", thread="thread", revoked=False):
    sid = str(uuid.uuid4())
    await store.voice_store.create(
        store.VitaVoiceSession(
            id=sid,
            user_id=user,
            thread_id=thread,
            conversation_id=f"conv_{sid}",
            agent_id="fixture-agent",
            language="es",
            revoked=revoked,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    return sid


async def clear(rig, user="owner", thread="thread"):
    return await rig.client.delete(f"/api/v1/threads/{thread}/state", params={"user_id": user})


async def test_manual_deletes_real_checkpoint_context_and_voice_preserves_other_owners(rig):
    cfg = await seed(rig)
    await locking.LockedGraph(rig.graph, "test").ainvoke(
        {"input": "fixture turn", "agent": "base"}, config=cfg
    )
    # Historial en otro namespace del mismo hilo también debe desaparecer.
    checkpoint = await rig.saver.aget_tuple(cfg)
    await rig.saver.aput(
        {"configurable": {"thread_id": "owner::thread", "checkpoint_ns": "fixture-subgraph"}},
        checkpoint.checkpoint,
        {"source": "update", "step": 0, "parents": {}},
        {},
    )
    await seed(rig, "other")
    await seed(rig, "owner", "erp-thread", opt_in=False)
    sid = await voice(rig)
    unrelated_sid = await voice(rig, user="other")
    await store.voice_store.start_turn(sid, 1, 1, "synthetic-prefix", "empty")
    # Sin consentimiento ni catálogo: borrar solo exige identidad confiable BACK.
    rig.monkeypatch.setattr(transcript, "read_catalog", AsyncMock(side_effect=AssertionError))
    response = await clear(rig)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["thread_id"] == "thread" and body["history_revision"] == 1
    assert body["reset_reason"] == "manual" and body["cleared_at"]
    assert (await rig.graph.aget_state(cfg)).created_at is None
    assert (await rig.graph.aget_state(config("other"))).values["messages"]
    assert (await rig.graph.aget_state(config("owner", "erp-thread"))).values["messages"]
    assert (await store.voice_store.get(unrelated_sid)).user_id == "other"
    async with rig.engine.connect() as sql:
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            assert (
                await sql.scalar(
                    text(f'SELECT count(*) FROM "{rig.schema}".{table} WHERE thread_id = :id'),
                    {"id": "owner::thread"},
                )
                == 0
            )
    for method in (
        store.voice_store.get(sid),
        store.voice_store.get_owned(sid, "owner", "thread"),
        store.voice_store.start_turn(sid, 1, 1, "synthetic-prefix", "empty"),
    ):
        with pytest.raises(store.VoiceContextError):
            await method
    state = await rig.client.get("/api/v1/threads/thread/state?user_id=owner")
    assert state.status_code == 200 and state.json()["messages"] == []
    assert all(state.json()[field] == body[field] for field in body)
    assert state.json()["message_count"] == 0 and state.json()["next_cursor"] is None
    assert not state.json()["has_more"]


async def test_internal_auth_and_identity_cannot_clear_another_owner(rig):
    cfg = await seed(rig)
    denied = await rig.client.delete(
        "/api/v1/threads/thread/state?user_id=owner", headers={"X-Internal-Key": "wrong"}
    )
    assert denied.status_code == 401
    other = await clear(rig, user="intruder")
    assert other.status_code == 200 and other.json()["history_revision"] == 0
    assert (await rig.graph.aget_state(cfg)).values["messages"]
    invalid = await clear(rig, user="owner::thread", thread="collision")
    assert invalid.status_code == 422
    missing = await rig.client.delete("/api/v1/threads/thread/state")
    assert missing.status_code == 422


async def test_repeated_empty_clear_is_idempotent_until_new_activity(rig):
    await seed(rig)
    first = (await clear(rig)).json()
    rig.now[0] += timedelta(hours=1)
    assert (await clear(rig)).json() == first
    cfg = config()
    await rig.history.prepare_turn("owner", "thread", rig.saver, cfg)
    await locking.LockedGraph(rig.graph, "test").ainvoke(
        {"input": "fresh fixture", "agent": "base"}, config=cfg
    )
    assert (await clear(rig)).json()["history_revision"] == 2
    # Persistencia/reinicio: objeto nuevo, la revisión nunca depende de caché.
    restarted = service.HistoryService(rig.sessions, clock=lambda: rig.now[0])
    assert (await restarted.get("owner", "thread")).history_revision == 2


async def test_calendar_month_sweep_without_open_app_and_restart_replica(rig):
    cfg = await seed(rig)
    await seed(rig, "owner", "erp-thread", opt_in=False)
    rig.now[0] = datetime(2026, 12, 1, 4, 59, 59, tzinfo=UTC)
    await rig.history.sweep(rig.saver)
    assert (await rig.graph.aget_state(cfg)).values["messages"]
    rig.now[0] = datetime(2026, 12, 1, 5, tzinfo=UTC)  # 00:00 Bogotá
    replica = service.HistoryService(rig.sessions, clock=lambda: rig.now[0])
    await asyncio.gather(rig.history.sweep(rig.saver), replica.sweep(rig.saver))
    assert (await rig.graph.aget_state(cfg)).created_at is None
    row = await replica.get("owner", "thread")
    assert row.history_revision == 1 and row.reset_reason == "monthly"
    assert row.processed_month.isoformat() == "2026-12-01"
    assert (await rig.graph.aget_state(config("owner", "erp-thread"))).values["messages"]
    rig.now[0] = datetime(2027, 1, 1, 5, tzinfo=UTC)
    await replica.sweep(rig.saver)
    row2 = await replica.get("owner", "thread")
    assert row2.history_revision == 1 and row2.cleared_at == row.cleared_at
    assert row2.processed_month.isoformat() == "2027-01-01"


async def test_pending_failure_is_visible_blocks_read_and_recovers_after_restart(rig):
    cfg = await seed(rig)
    sid = await voice(rig)
    original = rig.saver.adelete_thread
    rig.monkeypatch.setattr(
        rig.saver, "adelete_thread", AsyncMock(side_effect=RuntimeError("fixture"))
    )
    assert (await clear(rig)).status_code == 503
    row = await rig.history.get("owner", "thread")
    assert row.pending_reset and row.history_revision == 0
    assert (await rig.client.get("/api/v1/threads/thread/state?user_id=owner")).status_code == 503
    with pytest.raises(store.VoiceContextError):
        await store.voice_store.get(sid)
    with pytest.raises(RuntimeError):
        await rig.history.prepare_turn("owner", "thread", rig.saver, config())
    assert (await rig.graph.aget_state(cfg)).created_at  # Aún existe, pero no se expone.
    rig.monkeypatch.setattr(rig.saver, "adelete_thread", original)
    replica = service.HistoryService(rig.sessions, clock=lambda: rig.now[0])
    await replica.sweep(rig.saver)
    row = await replica.get("owner", "thread")
    assert not row.pending_reset and row.history_revision == 1 and row.reset_reason == "manual"
    assert (await rig.graph.aget_state(cfg)).created_at is None
    assert (await clear(rig)).json()["history_revision"] == 1


async def test_unconfirmed_saver_delete_never_returns_success(rig):
    await seed(rig)
    rig.monkeypatch.setattr(rig.saver, "adelete_thread", AsyncMock())
    response = await clear(rig)
    assert response.status_code == 503
    assert response.json()["detail"] == "history_checkpoint_delete_unconfirmed"
    assert (await rig.history.get("owner", "thread")).pending_reset


async def test_delete_excludes_running_turn_and_stale_invocation_and_stream(rig):
    cfg = await seed(rig)
    entered, release = asyncio.Event(), asyncio.Event()

    class PausedGraph:
        checkpointer = rig.saver

        async def ainvoke(self, *args, config, **kwargs):
            entered.set()
            await release.wait()
            return await rig.graph.ainvoke(*args, config=config, **kwargs)

    running = asyncio.create_task(
        locking.LockedGraph(PausedGraph(), "test").ainvoke(
            {"input": "fixture turn", "agent": "base"}, config=cfg
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert (await clear(rig)).status_code == 409
        release.set()
        await running
    finally:
        release.set()
        await asyncio.gather(running, return_exceptions=True)
    assert (await clear(rig)).status_code == 200
    locked = locking.LockedGraph(rig.graph, "test")
    for mode in ("invoke", "stream"):
        with pytest.raises(store.VoiceContextError, match="history_revision_changed"):
            if mode == "invoke":
                await locked.ainvoke({"input": "late"}, config=cfg)
            else:
                async for _ in locked.astream({"input": "late"}, config=cfg):
                    pytest.fail("No debe emitir eventos después de borrar")
    assert (await rig.graph.aget_state(cfg)).created_at is None


async def test_late_import_candidates_cannot_restore_cleared_history(rig):
    cfg = await seed(rig)
    sid = await voice(rig, revoked=True)
    candidates = await store.voice_store.closed_for_thread("owner", "thread")
    assert candidates[0].id == sid
    assert (await clear(rig)).status_code == 200
    rig.monkeypatch.setattr(
        store.voice_store, "closed_for_thread", AsyncMock(return_value=candidates)
    )
    fetch = AsyncMock(side_effect=AssertionError("La sesión borrada no se consulta al proveedor"))
    rig.monkeypatch.setattr(transcript, "fetch_transcript", fetch)
    rig.monkeypatch.setattr(transcript, "read_catalog", AsyncMock(return_value=[]))
    assert await transcript.sync_closed_thread("owner", "thread", rig.graph) == ("idle", True)
    fetch.assert_not_awaited()
    assert (await rig.graph.aget_state(cfg)).created_at is None


async def test_month_transition_revokes_voice_and_rejects_old_admission(rig):
    cfg = await seed(rig)
    sid = await voice(rig)
    cfg["configurable"]["voice_session_id"] = sid
    rig.now[0] = datetime(2026, 12, 1, 5, tzinfo=UTC)
    with pytest.raises(store.VoiceContextError, match="history_revision_changed"):
        await locking.LockedGraph(rig.graph, "test").ainvoke({"input": "late voice"}, config=cfg)
    with pytest.raises(store.VoiceContextError):
        await store.voice_store.get(sid)
    assert (await rig.history.get("owner", "thread")).reset_reason == "monthly"


async def test_http_custom_llm_uses_durable_history_and_clear_revokes_replay(rig):
    """Adaptador HTTP real + saver/recibos SQL, sin proveedor ni pacientes reales."""
    from app.vita.runtime import ResolvedTurn

    settings = get_settings()
    for name, value in {
        "voice_enabled": True,
        "vita_runtime_enabled": True,
        "vita_voice_enabled": True,
        "vita_voice_transport_verified": True,
        "vita_voice_service_key": "fixture-service-" * 3,
        "vita_voice_context_secret": "fixture-context-" * 3,
    }.items():
        rig.monkeypatch.setattr(settings, name, value)
    rig.monkeypatch.setattr(vita_voice, "history_service", rig.history)
    rig.monkeypatch.setattr(vita_voice, "read_catalog", AsyncMock(return_value=[]))

    async def resolve(**_):
        return ResolvedTurn(
            locking.LockedGraph(rig.graph, "fixture"), "base", None, "fake", "fake", 25, "fixture"
        )

    resolver = AsyncMock(side_effect=resolve)
    rig.monkeypatch.setattr(vita_voice, "resolve_turn", resolver)
    await seed(rig)
    sid = await voice(rig)
    record = await store.voice_store.get(sid)
    headers = {
        "Authorization": f"Bearer {settings.vita_voice_service_key}",
        "X-Vita-Session": sign_context(
            sid, int(record.expires_at.timestamp()), settings.vita_voice_context_secret
        ),
        "X-Vita-Conversation": record.conversation_id,
        "X-Vita-Turn": "1",
    }
    payload = {"stream": True, "messages": [{"role": "user", "content": "Hola"}]}
    path = "/internal/voice/v1/chat/completions"
    result = await rig.client.post(path, json=payload, headers=headers)
    assert result.status_code == 200 and "fixture response" in result.text
    replay = await rig.client.post(path, json=payload, headers=headers)
    assert replay.status_code == 200 and "fixture response" in replay.text
    resolver.assert_awaited_once()
    assert (await clear(rig)).json()["history_revision"] == 1
    rejected = await rig.client.post(path, json=payload, headers=headers)
    assert rejected.status_code == 403
    assert "fixture response" not in rejected.text
    resolver.assert_awaited_once()
    assert (await rig.graph.aget_state(config())).created_at is None


async def test_restart_after_checkpoint_deleted_before_revision_commit_finishes_once(rig):
    cfg = await seed(rig)
    async with rig.sessions() as session:
        row = await session.get(VitaHistory, ("owner", "thread"))
        row.pending_reset = True
        row.reset_reason = "manual"
        row.cleared_at = rig.now[0]
        await session.commit()
    await rig.saver.adelete_thread("owner::thread")
    replica = service.HistoryService(rig.sessions, clock=lambda: rig.now[0])
    async with locking.thread_guard("owner::thread"):
        row = await replica.ensure_locked("owner", "thread", rig.saver)
    assert row.history_revision == 1 and not row.pending_reset
    assert row.reset_reason == "manual" and (await rig.graph.aget_state(cfg)).created_at is None
    assert (await clear(rig)).json()["history_revision"] == 1


async def test_import_running_excludes_delete_then_clear_removes_imported_checkpoint(rig):
    cfg = await seed(rig)
    await voice(rig, revoked=True)
    entered, release = asyncio.Event(), asyncio.Event()

    async def fetch(_):
        entered.set()
        await release.wait()
        return transcript.VerifiedTranscript(
            [HumanMessage(id="fixture", content="official fixture")], "a" * 64
        )

    rig.monkeypatch.setattr(transcript, "fetch_transcript", fetch)
    rig.monkeypatch.setattr(transcript, "read_catalog", AsyncMock(return_value=[]))
    task = asyncio.create_task(transcript.sync_closed_thread("owner", "thread", rig.graph))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert (await clear(rig)).status_code == 409
        release.set()
        assert await task == ("synchronized", True)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert (await clear(rig)).json()["history_revision"] == 1
    assert (await rig.graph.aget_state(cfg)).created_at is None


async def test_projection_delete_is_owned_and_effective_without_checkpoint(rig):
    tid, other_tid = str(uuid.uuid4()), str(uuid.uuid4())
    async with rig.sessions() as session:
        session.add_all(
            [
                Thread(id=tid, user_id="owner", agent_type_id="base", title="fixture"),
                Thread(id=other_tid, user_id="other", agent_type_id="base", title="keep"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Message(thread_id=tid, role="user", content="synthetic fixture"),
                Message(thread_id=other_tid, role="user", content="unrelated fixture"),
            ]
        )
        await session.commit()
    assert (await clear(rig, thread=tid)).json()["history_revision"] == 1
    assert (await clear(rig, user="owner", thread=other_tid)).json()["history_revision"] == 0
    async with rig.sessions() as session:
        assert (
            await session.execute(select(Message).where(Message.thread_id == tid))
        ).scalars().all() == []
        assert (await session.get(Thread, tid)).title is None
        assert (await session.get(Thread, other_tid)).title == "keep"
        assert (
            await session.execute(select(Message).where(Message.thread_id == other_tid))
        ).scalar_one()


@pytest.mark.parametrize("stream", [False, True])
async def test_chat_admission_before_clear_cannot_generate_even_without_checkpoint(rig, stream):
    entered, release = asyncio.Event(), asyncio.Event()

    async def resolve(*_):
        entered.set()
        await release.wait()
        return rig.graph, 25, None, None, None

    rig.monkeypatch.setattr(chat, "_resolve_graph", resolve)
    request = asyncio.create_task(
        rig.client.post(
            "/api/v1/chat/stream" if stream else "/api/v1/chat",
            json={
                "message": "late fixture",
                "user_id": "owner",
                "thread_id": "thread",
                "supports_vita": True,
            },
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert (await clear(rig)).json()["history_revision"] == 1
        release.set()
        response = await request
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)
    if stream:
        assert response.status_code == 200 and "event: error" in response.text
    else:
        assert response.status_code == 409
        assert response.json()["detail"] == "history_revision_changed"
    assert rig.model.prompts == []
    assert (await rig.graph.aget_state(config())).created_at is None


async def test_reserve_binding_is_opt_in_and_uses_month_guard(rig):
    from app.voice.schemas import VoiceSessionRequest

    payload = VoiceSessionRequest(user_id="owner", thread_id="thread", supports_vita_voice=True)
    record = await vita_session.reserve_binding(
        payload,
        {"conversation_id": "conv_fixture_new"},
        agent_id="fixture",
        version_id=None,
        runtime_mode="legacy",
    )
    assert (await rig.history.get("owner", "thread")).dirty
    assert (await clear(rig)).json()["history_revision"] == 1
    with pytest.raises(store.VoiceContextError):
        await store.voice_store.get(record.id)


async def test_voice_reservation_during_cleanup_is_retryable_without_creating_binding(rig):
    from app.voice.schemas import VoiceSessionRequest

    payload = VoiceSessionRequest(user_id="owner", thread_id="thread", supports_vita_voice=True)
    async with locking.thread_guard("owner::thread"):
        with pytest.raises(store.VoiceContextError, match="vita_thread_busy") as exc:
            await vita_session.reserve_binding(
                payload,
                {"conversation_id": "conv_fixture_busy"},
                agent_id="fixture",
                version_id=None,
                runtime_mode="legacy",
            )
    assert exc.value.status == 409
    async with rig.sessions() as session:
        assert (await session.execute(select(store.VitaVoiceSession))).scalars().all() == []


async def test_sweep_cursor_avoids_starvation_behind_busy_first_page(rig):
    await seed(rig, "a-busy")
    cfg = await seed(rig, "z-available")
    rig.now[0] = datetime(2026, 12, 1, 5, tzinfo=UTC)
    async with locking.thread_guard("a-busy::thread"):
        after = await rig.history.sweep(rig.saver, limit=1)
        assert after == ("a-busy", "thread")
        await rig.history.sweep(rig.saver, limit=1, after=after)
        assert (await rig.graph.aget_state(cfg)).created_at is None
    await rig.history.sweep(rig.saver)
    assert (await rig.history.get("a-busy", "thread")).history_revision == 1


def test_history_migration_upgrade_downgrade_without_backfill():
    url = isolated_test_url()
    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.stamp(cfg, "fe62a09c1741")
    engine = create_engine(url)
    try:
        command.upgrade(cfg, "18c4f6a91b20")
        with engine.connect() as sql:
            assert "vita_history" in inspect(sql).get_table_names(schema="ai")
            assert sql.scalar(text("SELECT count(*) FROM ai.vita_history")) == 0
            assert inspect(sql).get_pk_constraint("vita_history", schema="ai")[
                "constrained_columns"
            ] == ["user_id", "thread_id"]
        command.downgrade(cfg, "fe62a09c1741")
        with engine.connect() as sql:
            assert "vita_history" not in inspect(sql).get_table_names(schema="ai")
        command.upgrade(cfg, "18c4f6a91b20")
        command.downgrade(cfg, "fe62a09c1741")
    finally:
        engine.dispose()
