"""Integración opt-in. Exige una DB de tests local aislada, nunca la BD compartida.

VITA_TEST_DATABASE_URL=postgresql+psycopg://.../vita_test uv run pytest tests/test_vita_postgres.py
No se ejecuta por defecto ni acredita réplica real cuando queda skipped.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.vita import locking
from app.voice import store


async def test_two_repository_instances_share_revocation_receipts_and_thread_lock(monkeypatch):
    url = os.getenv("VITA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Falta VITA_TEST_DATABASE_URL aislada; no se toca BD compartida")
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
                language="es",
                revoked=False,
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
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
        with pytest.raises(store.VoiceContextError, match="inactive"):
            await other_replica.get(sid)
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()
