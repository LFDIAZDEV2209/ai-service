BEGIN;

-- Running downgrade fe62a09c1741 -> e7041890ceaa

DROP TABLE ai.vita_voice_turns;

DROP TABLE ai.vita_voice_sessions;

UPDATE ai.alembic_version SET version_num='e7041890ceaa' WHERE ai.alembic_version.version_num = 'fe62a09c1741';

COMMIT;
