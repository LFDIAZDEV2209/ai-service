BEGIN;

-- Running downgrade 18c4f6a91b20 -> fe62a09c1741

DROP TABLE ai.vita_history;

UPDATE ai.alembic_version SET version_num='fe62a09c1741' WHERE ai.alembic_version.version_num = '18c4f6a91b20';

COMMIT;
