BEGIN;

-- Running upgrade fe62a09c1741 -> 18c4f6a91b20

CREATE TABLE ai.vita_history (
    user_id VARCHAR(128) NOT NULL,
    thread_id VARCHAR(128) NOT NULL,
    processed_month DATE NOT NULL,
    history_revision INTEGER DEFAULT '0' NOT NULL,
    dirty BOOLEAN DEFAULT false NOT NULL,
    pending_reset BOOLEAN DEFAULT false NOT NULL,
    cleared_at TIMESTAMP WITH TIME ZONE,
    reset_reason VARCHAR(16),
    CONSTRAINT pk_vita_history PRIMARY KEY (user_id, thread_id),
    CONSTRAINT ck_vita_history_ck_vita_history_revision CHECK (history_revision >= 0)
);

CREATE INDEX ix_vita_history_month ON ai.vita_history (processed_month);

UPDATE ai.alembic_version SET version_num='18c4f6a91b20' WHERE ai.alembic_version.version_num = 'fe62a09c1741';

COMMIT;
