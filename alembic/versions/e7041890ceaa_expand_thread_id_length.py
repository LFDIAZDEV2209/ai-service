"""expand_thread_id_length

Revision ID: e7041890ceaa
Revises: 27d66edb1468
Create Date: 2026-08-27 09:59:23.748719

"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = 'e7041890ceaa'
down_revision = '27d66edb1468'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('agent_executions', 'thread_id',
               existing_type=sa.String(length=36),
               type_=sa.String(length=128),
               existing_nullable=True,
               schema='ai')
    op.alter_column('agent_executions', 'user_id',
               existing_type=sa.String(length=36),
               type_=sa.String(length=128),
               existing_nullable=True,
               schema='ai')
    op.alter_column('agent_executions', 'agent_type_id',
               existing_type=sa.String(length=36),
               type_=sa.String(length=128),
               existing_nullable=False,
               schema='ai')
    op.alter_column('agent_executions', 'version_id',
               existing_type=sa.String(length=36),
               type_=sa.String(length=128),
               existing_nullable=True,
               schema='ai')
    op.alter_column('agent_executions', 'agent_instance_id',
               existing_type=sa.String(length=36),
               type_=sa.String(length=128),
               existing_nullable=True,
               schema='ai')

    op.alter_column('agent_feedback', 'thread_id',
               existing_type=sa.String(length=36),
               type_=sa.String(length=128),
               existing_nullable=True,
               schema='ai')
    op.alter_column('agent_feedback', 'user_id',
               existing_type=sa.String(length=36),
               type_=sa.String(length=128),
               existing_nullable=True,
               schema='ai')

    op.alter_column('agent_memories', 'user_id',
               existing_type=sa.String(length=36),
               type_=sa.String(length=128),
               existing_nullable=True,
               schema='ai')
    op.alter_column('agent_memories', 'agent_instance_id',
               existing_type=sa.String(length=36),
               type_=sa.String(length=128),
               existing_nullable=True,
               schema='ai')

    op.alter_column('threads', 'user_id',
               existing_type=sa.String(length=36),
               type_=sa.String(length=128),
               existing_nullable=True,
               schema='ai')
    op.alter_column('threads', 'agent_instance_id',
               existing_type=sa.String(length=36),
               type_=sa.String(length=128),
               existing_nullable=True,
               schema='ai')


def downgrade() -> None:
    op.alter_column('threads', 'agent_instance_id',
               existing_type=sa.String(length=128),
               type_=sa.String(length=36),
               existing_nullable=True,
               schema='ai')
    op.alter_column('threads', 'user_id',
               existing_type=sa.String(length=128),
               type_=sa.String(length=36),
               existing_nullable=True,
               schema='ai')

    op.alter_column('agent_memories', 'agent_instance_id',
               existing_type=sa.String(length=128),
               type_=sa.String(length=36),
               existing_nullable=True,
               schema='ai')
    op.alter_column('agent_memories', 'user_id',
               existing_type=sa.String(length=128),
               type_=sa.String(length=36),
               existing_nullable=True,
               schema='ai')

    op.alter_column('agent_feedback', 'user_id',
               existing_type=sa.String(length=128),
               type_=sa.String(length=36),
               existing_nullable=True,
               schema='ai')
    op.alter_column('agent_feedback', 'thread_id',
               existing_type=sa.String(length=128),
               type_=sa.String(length=36),
               existing_nullable=True,
               schema='ai')

    op.alter_column('agent_executions', 'agent_instance_id',
               existing_type=sa.String(length=128),
               type_=sa.String(length=36),
               existing_nullable=True,
               schema='ai')
    op.alter_column('agent_executions', 'version_id',
               existing_type=sa.String(length=128),
               type_=sa.String(length=36),
               existing_nullable=True,
               schema='ai')
    op.alter_column('agent_executions', 'agent_type_id',
               existing_type=sa.String(length=128),
               type_=sa.String(length=36),
               existing_nullable=False,
               schema='ai')
    op.alter_column('agent_executions', 'user_id',
               existing_type=sa.String(length=128),
               type_=sa.String(length=36),
               existing_nullable=True,
               schema='ai')
    op.alter_column('agent_executions', 'thread_id',
               existing_type=sa.String(length=128),
               type_=sa.String(length=36),
               existing_nullable=True,
               schema='ai')
