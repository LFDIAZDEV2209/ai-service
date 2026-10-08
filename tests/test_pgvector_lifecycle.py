"""El runtime filtra el catálogo autoritativo antes de recuperar chunks residuales."""
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects import postgresql

from app.rag.pgvector_store import PgVectorStore


async def test_search_requires_ready_document_and_active_matching_knowledge_base():
    session = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=result)
    await PgVectorStore(session).search([0.0] * 1536, 5, knowledge_base_ids=["owned-kb"])
    stmt = session.execute.call_args.args[0]
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "agents.documents" in sql
    assert "d.status = 'Listo'" in sql
    assert "kb.status = 'Activo'" in sql
    assert "kb.id::text = ai.knowledge_chunks.knowledge_base_id" in sql
    assert "owned-kb" in sql
    assert "LIMIT 5" in sql
