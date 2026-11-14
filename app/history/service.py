"""Limpieza verificable bajo el MISMO thread_guard de ejecución/importación.

El tombstone se confirma antes de tocar el saver (otra conexión). Un fallo o
reinicio intermedio deja pending_reset durable: ninguna entrada puede reutilizar
el contexto antes de terminar el borrado. La revisión se confirma una sola vez.
Los métodos *_locked requieren que el llamador mantenga thread_guard.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete, or_, select, tuple_, update

from app.db.engine import async_session
from app.db.models import Message, Thread
from app.history.store import VitaHistory
from app.vita.locking import ThreadBusyError, thread_guard
from app.voice.store import VitaVoiceSession, VoiceContextError, voice_store

logger = logging.getLogger(__name__)
# Bogotá no observa DST. Offset fijo evita depender de tzdata del sistema Windows.
BOGOTA = timezone(timedelta(hours=-5), "America/Bogota")


def calendar_month(now: datetime) -> date:
    if now.tzinfo is None:
        raise ValueError("history_clock_requires_timezone")
    return now.astimezone(BOGOTA).date().replace(day=1)


def storage_key(user_id: str, thread_id: str) -> str:
    # No aceptar delimitadores capaces de colisionar con otra identidad.
    if not user_id or not thread_id or "::" in user_id or "::" in thread_id:
        raise VoiceContextError("history_identity_invalid", 422)
    if len(user_id) > 128 or len(thread_id) > 128:
        raise VoiceContextError("history_identity_invalid", 422)
    return f"{user_id}::{thread_id}"


def metadata(record: VitaHistory | None) -> dict:
    return {
        "history_revision": record.history_revision if record else 0,
        "cleared_at": record.cleared_at if record else None,
        "reset_reason": record.reset_reason if record else None,
    }


class HistoryService:
    def __init__(self, session_factory=async_session, clock=None):
        self.sessions = session_factory
        self.clock = clock or (lambda: datetime.now(UTC))

    async def get(self, user_id: str, thread_id: str) -> VitaHistory | None:
        async with self.sessions() as session:
            return await session.get(VitaHistory, (user_id, thread_id))

    async def ensure_locked(
        self, user_id: str, thread_id: str, saver, *, opt_in=False
    ) -> VitaHistory | None:
        record = await self.get(user_id, thread_id)
        if record is None:
            if not opt_in:
                return None
            storage_key(user_id, thread_id)
            async with self.sessions() as session:
                record = VitaHistory(
                    user_id=user_id,
                    thread_id=thread_id,
                    processed_month=calendar_month(self.clock()),
                    history_revision=0,
                    dirty=False,
                    pending_reset=False,
                )
                session.add(record)
                await session.commit()
        if record.pending_reset or record.processed_month < calendar_month(self.clock()):
            return await self.clear_locked(user_id, thread_id, saver, reason="monthly")
        return record

    async def clear_locked(
        self, user_id: str, thread_id: str, saver, *, reason="manual"
    ) -> VitaHistory:
        key = storage_key(user_id, thread_id)
        record = await self.get(user_id, thread_id)
        if record is None:
            record = await self.ensure_locked(user_id, thread_id, saver, opt_in=True)
        config = {"configurable": {"thread_id": key}}
        # alist sin namespace cubre también checkpoints de subgrafos.
        has_checkpoint = bool([item async for item in saver.alist(config, limit=1)])
        try:
            public_id = str(UUID(thread_id))
        except ValueError:
            public_id = None
        async with self.sessions() as session:
            has_voice = await session.scalar(
                select(VitaVoiceSession.id)
                .where(VitaVoiceSession.user_id == user_id, VitaVoiceSession.thread_id == thread_id)
                .limit(1)
            )
            owned = select(Thread.id).where(Thread.id == public_id, Thread.user_id == user_id)
            has_projection = (
                await session.scalar(
                    select(Message.id).where(Message.thread_id.in_(owned)).limit(1)
                )
                if public_id
                else None
            )
            record = await session.get(VitaHistory, (user_id, thread_id))
            effective = (
                record.pending_reset
                or record.dirty
                or has_checkpoint
                or bool(has_voice)
                or bool(has_projection)
            )
            if not record.pending_reset and (effective or record.cleared_at is None):
                record.pending_reset = effective
                record.reset_reason = reason
                record.cleared_at = self.clock()
                # La ausencia del vínculo revoca tokens, recibos e importaciones
                # incluso si el saver falla después de este commit.
                await session.execute(
                    delete(VitaVoiceSession).where(
                        VitaVoiceSession.user_id == user_id,
                        VitaVoiceSession.thread_id == thread_id,
                    )
                )
                await session.commit()
        if effective:
            await saver.adelete_thread(key)
            if [item async for item in saver.alist(config, limit=1)]:
                raise VoiceContextError("history_checkpoint_delete_unconfirmed", 503)
        async with self.sessions() as session:
            record = await session.get(VitaHistory, (user_id, thread_id))
            # Proyección de negocio: solo UUID + propietario exacto, nunca SQL
            # contra tablas de planes/documentos/memoria clínica.
            if public_id:
                owned = select(Thread.id).where(Thread.id == public_id, Thread.user_id == user_id)
                await session.execute(delete(Message).where(Message.thread_id.in_(owned)))
                await session.execute(
                    update(Thread)
                    .where(Thread.id == public_id, Thread.user_id == user_id)
                    .values(title=None, extra=None, last_message_at=None)
                )
            if effective:
                record.history_revision += 1
            record.dirty = False
            record.pending_reset = False
            record.processed_month = calendar_month(self.clock())
            await session.commit()
            return record

    async def activity_locked(self, user_id: str, thread_id: str) -> None:
        async with self.sessions() as session:
            record = await session.get(VitaHistory, (user_id, thread_id))
            if record is None or record.pending_reset:
                raise VoiceContextError("history_not_ready", 503)
            record.dirty = True
            await session.commit()

    async def prepare_turn(self, user_id: str, thread_id: str, saver, config: dict) -> None:
        """Opt-in confiable de BACK. Llamar ANTES de resolver el runtime/routing."""
        async with thread_guard(storage_key(user_id, thread_id)):
            record = await self.ensure_locked(user_id, thread_id, saver, opt_in=True)
            if session_id := config["configurable"].get("voice_session_id"):
                voice = await voice_store.get(session_id)
                if voice.user_id != user_id or voice.thread_id != thread_id:
                    raise VoiceContextError("history_voice_binding_invalid", 403)
            # Admitir un turno es actividad: un DELETE posterior también debe
            # invalidar el primer request, aunque aún no exista checkpoint.
            await self.activity_locked(user_id, thread_id)
            config["configurable"]["vita_history"] = {
                "user_id": user_id,
                "thread_id": thread_id,
                "revision": record.history_revision,
            }

    async def before_turn_locked(self, graph, config: dict) -> None:
        binding = config["configurable"].get("vita_history")
        if binding is None:
            return
        user_id, thread_id = binding["user_id"], binding["thread_id"]
        if storage_key(user_id, thread_id) != config["configurable"]["thread_id"]:
            raise VoiceContextError("history_binding_invalid", 403)
        record = await self.ensure_locked(user_id, thread_id, graph.checkpointer)
        if record is None or record.history_revision != binding["revision"]:
            raise VoiceContextError("history_revision_changed", 409)
        if session_id := config["configurable"].get("voice_session_id"):
            voice = await voice_store.get(session_id)
            if voice.user_id != user_id or voice.thread_id != thread_id:
                raise VoiceContextError("history_voice_binding_invalid", 403)
        await self.activity_locked(user_id, thread_id)

    async def sweep(self, saver, *, limit=100, after=None) -> tuple[str, str] | None:
        """Una página acotada; el mes persistido y advisory lock coordinan réplicas."""
        async with self.sessions() as session:
            query = (
                select(VitaHistory.user_id, VitaHistory.thread_id)
                .where(
                    or_(
                        VitaHistory.processed_month < calendar_month(self.clock()),
                        VitaHistory.pending_reset.is_(True),
                    )
                )
                .order_by(VitaHistory.user_id, VitaHistory.thread_id)
                .limit(limit)
            )
            if after is not None:
                query = query.where(tuple_(VitaHistory.user_id, VitaHistory.thread_id) > after)
            candidates = (await session.execute(query)).all()
        for user_id, thread_id in candidates:
            try:
                async with thread_guard(storage_key(user_id, thread_id)):
                    await self.ensure_locked(user_id, thread_id, saver)
            except ThreadBusyError:
                continue  # El turno o la réplica que ganó completa/reintenta el corte.
            except Exception:
                logger.error("vita_history_sweep_item_failed")
        # Cursor evita que fallos/bloqueos permanentes en la primera página
        # impidan procesar todos los demás hilos. Se reinicia al terminar vuelta.
        return tuple(candidates[-1]) if len(candidates) == limit else None


history_service = HistoryService()


@asynccontextmanager
async def retention_worker():
    """El proceso no puede olvidar meses al reiniciar; no depende de la app abierta."""
    from app.memory.checkpointer import get_checkpointer

    async def run():
        after = None
        while True:
            try:
                async with asyncio.timeout(45):
                    after = await history_service.sweep(await get_checkpointer(), after=after)
            except Exception:
                logger.error("vita_history_sweep_failed")
            await asyncio.sleep(30)

    task = asyncio.create_task(run(), name="vita-history-retention")
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
