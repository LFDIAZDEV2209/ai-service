"""Integración opt-in. Exige una DB de tests local aislada, nunca la BD compartida.

VITA_TEST_DATABASE_URL=postgresql+psycopg://.../vita_test uv run pytest tests/test_vita_postgres.py
No se ejecuta por defecto ni acredita réplica real cuando queda skipped.
"""

import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.vita import locking
from app.voice import store


async def test_two_repository_instances_share_revocation_receipts_and_thread_lock(monkeypatch):
    url = os.getenv("VITA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Falta VITA_TEST_DATABASE_URL aislada; no se toca BD compartida")
    if sys.platform == "win32":
        assert isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)
    parsed = make_url(url)
    if (
        parsed.host not in {"localhost", "127.0.0.1"}
        or "test" not in (parsed.database or "").lower()
    ):
        pytest.fail("VITA_TEST_DATABASE_URL debe apuntar a una DB local de tests aislada")
    schema = f"vita_contract_{uuid.uuid4().hex}"
    engine = create_async_engine(url, execution_options={"schema_translate_map": {"ai": schema}})
    monkeypatch.setattr(store, "async_session", async_sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(locking, "engine", engine)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.run_sync(
                lambda sync: store.Base.metadata.create_all(
                    sync,
                    tables=[store.VitaVoiceSession.__table__, store.VitaVoiceTurn.__table__],
                )
            )
        first, other_replica = store.VoiceStore(), store.VoiceStore()
        sid = str(uuid.uuid4())
        await first.create(
            store.VitaVoiceSession(
                id=sid,
                user_id="fixture-owner",
                patient_id=None,
                thread_id="fixture-thread",
                conversation_id=f"conv_{sid}",
                agent_id="agent_fixture",
                language="es",
                revoked=False,
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        assert await first.closed_for_thread("fixture-owner", "fixture-thread") == []
        with pytest.raises(store.VoiceContextError, match="not_found"):
            await other_replica.get_owned(sid, "other", "fixture-thread")
        results = await asyncio.gather(
            first.start_turn(sid, 1, 1, "prefix-one", "empty"),
            other_replica.start_turn(sid, 1, 1, "prefix-one", "empty"),
            return_exceptions=True,
        )
        assert sum(isinstance(r, store.VitaVoiceTurn) for r in results) == 1
        assert sum(isinstance(r, store.VoiceContextError) for r in results) == 1
        await first.finish_turn(sid, 1, "completed")
        assert (
            await other_replica.start_turn(sid, 1, 1, "prefix-one", "empty")
        ).status == "completed"
        await other_replica.start_turn(sid, 2, 2, "prefix-two", "prefix-one")
        async with locking.thread_guard("fixture-owner::fixture-thread"):
            with pytest.raises(locking.ThreadBusyError):
                async with locking.thread_guard("fixture-owner::fixture-thread"):
                    pytest.fail("El lock no excluyó otra conexión")
        async with locking.thread_guard("fixture-owner::fixture-thread"):
            pass  # Se libera al terminar la transacción.
        with pytest.raises(store.VoiceContextError, match="not_found"):
            await other_replica.revoke(sid, "foreign-owner")
        await first.revoke(sid, "fixture-owner")
        assert len(await other_replica.closed_for_thread("fixture-owner", "fixture-thread")) == 1
        assert await other_replica.closed_for_thread("other", "fixture-thread") == []
        await first.mark_transcript_synced(sid, "a" * 64)
        bound = await other_replica.get_owned(sid, "fixture-owner", "fixture-thread")
        assert bound.agent_id == "agent_fixture" and bound.transcript_digest == "a" * 64
        assert bound.transcript_synced_at is not None
        with pytest.raises(store.VoiceContextError, match="inactive"):
            await other_replica.get(sid)
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


def test_voice_migration_upgrade_and_rollback_in_disposable_database():
    url = os.getenv("VITA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Falta VITA_TEST_DATABASE_URL aislada; no se toca BD compartida")
    parsed = make_url(url)
    if (
        parsed.host not in {"localhost", "127.0.0.1"}
        or "test" not in (parsed.database or "").lower()
    ):
        pytest.fail("Se requiere DB local de tests aislada")
    root = Path(__file__).resolve().parent.parent
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    # Baseline sintética para aplicar exclusivamente la nueva revisión; no se
    # recrean tablas del producto ni se necesita pgvector para estas dos tablas.
    command.stamp(config, "e7041890ceaa")
    command.upgrade(config, "fe62a09c1741")
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert {"vita_voice_sessions", "vita_voice_turns"} <= set(
                inspector.get_table_names(schema="ai")
            )
            assert inspector.get_pk_constraint("vita_voice_turns", schema="ai")[
                "constrained_columns"
            ] == ["session_id", "ordinal"]
            assert (
                inspector.get_foreign_keys("vita_voice_turns", schema="ai")[0]["referred_table"]
                == "vita_voice_sessions"
            )
        command.downgrade(config, "e7041890ceaa")
        with engine.connect() as connection:
            assert "vita_voice_sessions" not in inspect(connection).get_table_names(schema="ai")
            assert "vita_voice_turns" not in inspect(connection).get_table_names(schema="ai")
    finally:
        engine.dispose()
