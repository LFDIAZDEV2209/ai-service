"""Sesiones/recibos de voz compartidos entre réplicas, sin transcripciones duplicadas."""

import sqlalchemy as sa

from alembic import op

revision = "fe62a09c1741"
down_revision = "e7041890ceaa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vita_voice_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("patient_id", sa.String(128)),
        sa.Column("thread_id", sa.String(128), nullable=False),
        sa.Column("conversation_id", sa.String(128), nullable=False, unique=True),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="ai",
    )
    op.create_index("ix_ai_vita_voice_sessions_user_id", "vita_voice_sessions", ["user_id"], schema="ai")
    op.create_index("ix_ai_vita_voice_sessions_expires_at", "vita_voice_sessions", ["expires_at"], schema="ai")
    op.create_table(
        "vita_voice_turns",
        sa.Column("session_id", sa.String(36), primary_key=True),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column("provider_turn", sa.Integer(), nullable=False),
        sa.Column("prefix_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("response_message_id", sa.String(128), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["ai.vita_voice_sessions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("session_id", "provider_turn"), schema="ai",
    )


def downgrade() -> None:
    op.drop_table("vita_voice_turns", schema="ai")
    op.drop_table("vita_voice_sessions", schema="ai")
