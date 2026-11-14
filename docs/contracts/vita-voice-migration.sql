BEGIN;

-- Running upgrade e7041890ceaa -> fe62a09c1741

CREATE TABLE ai.vita_voice_sessions (
    id VARCHAR(36) NOT NULL,
    user_id VARCHAR(128) NOT NULL,
    patient_id VARCHAR(128),
    thread_id VARCHAR(128) NOT NULL,
    conversation_id VARCHAR(128) NOT NULL,
    agent_id VARCHAR(128) NOT NULL,
    version_id VARCHAR(128),
    runtime_mode VARCHAR(16) DEFAULT 'vita' NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    transcript_digest VARCHAR(64),
    transcript_synced_at TIMESTAMP WITH TIME ZONE,
    language VARCHAR(2) NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    revoked BOOLEAN DEFAULT false NOT NULL,
    CONSTRAINT pk_vita_voice_sessions PRIMARY KEY (id),
    CONSTRAINT uq_vita_voice_sessions_conversation_id UNIQUE (conversation_id)
);

CREATE INDEX ix_ai_vita_voice_sessions_user_id ON ai.vita_voice_sessions (user_id);

CREATE INDEX ix_ai_vita_voice_sessions_expires_at ON ai.vita_voice_sessions (expires_at);

CREATE INDEX ix_vita_voice_sessions_owner_thread_created ON ai.vita_voice_sessions (user_id, thread_id, created_at);

CREATE TABLE ai.vita_voice_turns (
    session_id VARCHAR(36) NOT NULL,
    ordinal INTEGER NOT NULL,
    provider_turn INTEGER NOT NULL,
    prefix_hash VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL,
    response_message_id VARCHAR(128) NOT NULL,
    CONSTRAINT pk_vita_voice_turns PRIMARY KEY (session_id, ordinal),
    CONSTRAINT fk_vita_voice_turns_session_id_vita_voice_sessions FOREIGN KEY(session_id) REFERENCES ai.vita_voice_sessions (id) ON DELETE CASCADE,
    CONSTRAINT uq_vita_voice_turns_session_id UNIQUE (session_id, provider_turn)
);

UPDATE ai.alembic_version SET version_num='fe62a09c1741' WHERE ai.alembic_version.version_num = 'e7041890ceaa';

COMMIT;
