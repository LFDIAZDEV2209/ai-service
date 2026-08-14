"""Paquete de acceso a datos del AI Service (schema `ai`)."""

from app.db.base import Base
from app.db.engine import async_session, engine, sqlalchemy_url

__all__ = ["Base", "async_session", "engine", "sqlalchemy_url"]
