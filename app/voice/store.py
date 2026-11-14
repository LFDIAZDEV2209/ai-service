"""Sesiones y recibos durables: ninguna garantía depende del proceso que atiende."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.engine import async_session


class VitaVoiceSession(Base):
    __tablename__ = "vita_voice_sessions"
    __table_args__ = ({"schema": "ai"},)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    patient_id: Mapped[str | None] = mapped_column(String(128))
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class VitaVoiceTurn(Base):
    __tablename__ = "vita_voice_turns"
    __table_args__ = (
        UniqueConstraint("session_id", "provider_turn"),
        {"schema": "ai"},
    )

    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("ai.vita_voice_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_turn: Mapped[int] = mapped_column(Integer, nullable=False)
    prefix_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    response_message_id: Mapped[str] = mapped_column(String(128), nullable=False)


class VoiceContextError(Exception):
    """Error público acotado, nunca contiene contexto/token/historial."""

    def __init__(self, code: str, status: int = 403):
        super().__init__(code)
        self.status = status


class VoiceStore:
    async def create(self, record: VitaVoiceSession) -> None:
        async with async_session() as session:
            session.add(record)
            await session.commit()

    async def get(self, session_id: str) -> VitaVoiceSession:
        async with async_session() as session:
            record = await session.get(VitaVoiceSession, session_id)
            if record is None or record.revoked or record.expires_at <= datetime.now(UTC):
                raise VoiceContextError("voice_session_inactive")
            return record

    async def revoke(self, session_id: str, user_id: str) -> None:
        async with async_session() as session:
            record = await session.get(VitaVoiceSession, session_id, with_for_update=True)
            if record is None or record.user_id != user_id:
                raise VoiceContextError("voice_session_not_found", 404)
            record.revoked = True
            await session.commit()

    async def start_turn(
        self,
        session_id: str,
        ordinal: int,
        provider_turn: int,
        prefix_hash: str,
        previous_hash: str,
    ) -> VitaVoiceTurn:
        async with async_session() as session:
            # Una fila de sesión serializa decisiones de ordinal entre réplicas.
            record = await session.get(VitaVoiceSession, session_id, with_for_update=True)
            if record is None or record.revoked or record.expires_at <= datetime.now(UTC):
                raise VoiceContextError("voice_session_inactive")
            latest = (
                await session.execute(
                    select(VitaVoiceTurn)
                    .where(VitaVoiceTurn.session_id == session_id)
                    .order_by(VitaVoiceTurn.ordinal.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            existing = await session.get(VitaVoiceTurn, (session_id, ordinal))
            if existing:
                if existing.prefix_hash != prefix_hash or existing.provider_turn != provider_turn:
                    raise VoiceContextError("voice_turn_conflict", 409)
                if existing.status != "completed":
                    raise VoiceContextError("voice_turn_inflight_or_interrupted", 409)
                return existing
            if ordinal != (latest.ordinal + 1 if latest else 1):
                raise VoiceContextError("voice_history_sequence", 409)
            if latest and (
                latest.prefix_hash != previous_hash or provider_turn <= latest.provider_turn
            ):
                raise VoiceContextError("voice_history_changed", 409)
            turn = VitaVoiceTurn(
                session_id=session_id,
                ordinal=ordinal,
                provider_turn=provider_turn,
                prefix_hash=prefix_hash,
                status="running",
                response_message_id=f"voice:{session_id}:{ordinal}:bot",
            )
            session.add(turn)
            await session.commit()
            return turn

    async def finish_turn(self, session_id: str, ordinal: int, status: str) -> None:
        async with async_session() as session:
            turn = await session.get(VitaVoiceTurn, (session_id, ordinal), with_for_update=True)
            if turn and turn.status == "running":
                turn.status = status
                await session.commit()


voice_store = VoiceStore()
