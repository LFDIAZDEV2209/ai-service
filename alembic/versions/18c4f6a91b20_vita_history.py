"""Retención mensual Vita opt-in, sin backfill ni modificaciones clínicas.

Rollback: elimina solo el registro de retención; no recupera mensajes borrados.
Detener el barrido/entradas Vita antes de downgrade. Tabla nueva y vacía al subir.
"""

import sqlalchemy as sa

from alembic import op

revision = "18c4f6a91b20"
down_revision = "fe62a09c1741"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vita_history",
        sa.Column("user_id", sa.String(128), primary_key=True),
        sa.Column("thread_id", sa.String(128), primary_key=True),
        sa.Column("processed_month", sa.Date(), nullable=False),
        sa.Column("history_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dirty", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pending_reset", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cleared_at", sa.DateTime(timezone=True)),
        sa.Column("reset_reason", sa.String(16)),
        sa.CheckConstraint("history_revision >= 0", name="ck_vita_history_revision"),
        schema="ai",
    )
    op.create_index("ix_vita_history_month", "vita_history", ["processed_month"], schema="ai")


def downgrade() -> None:
    op.drop_table("vita_history", schema="ai")
