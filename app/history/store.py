"""Registro durable mínimo de retención, sin contenido de pacientes."""

from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VitaHistory(Base):
    __tablename__ = "vita_history"
    __table_args__ = (
        Index("ix_vita_history_month", "processed_month"),
        CheckConstraint("history_revision >= 0", name="ck_vita_history_revision"),
        {"schema": "ai"},
    )

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    processed_month: Mapped[date] = mapped_column(Date, nullable=False)
    history_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dirty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pending_reset: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reset_reason: Mapped[str | None] = mapped_column(String(16))
