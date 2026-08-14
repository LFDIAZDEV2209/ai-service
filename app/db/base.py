"""Base declarativa de SQLAlchemy para el schema `ai`.

Define la convención de nombres de constraints (snake_case) para que las
restricciones generadas por Alembic sigan el mismo estilo que el backend EF.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMED_CONVENTIONS = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base declarativa del servicio de IA (solo schema `ai`)."""

    metadata = MetaData(naming_convention=NAMED_CONVENTIONS)
