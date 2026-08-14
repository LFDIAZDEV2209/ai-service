"""Almacén vectorial sobre pgvector (PostgreSQL) — implementación de producción.

Reemplaza al `InMemoryVectorStore` (solo desarrollo/tests): los chunks viven en
`ai.knowledge_chunks` con su embedding y metadata, buscables con similitud
coseno vía el índice HNSW ya creado por la migración inicial.

Filtrado por metadata: las búsquedas aceptan `knowledge_base_id`/`document_id`
para restringir la recuperación al conocimiento del agente (y nunca mezclar
KBs entre agentes).
"""

from __future__ import annotations

import numpy as np
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KnowledgeChunk
from app.rag.chunker import Chunk


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Similitud coseno entre dos vectores (embeddings normalizados)."""
    va = np.asarray(a, dtype=np.float64)
    vb = np.asarray(b, dtype=np.float64)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0.0:
        return 0.0
    return float(np.dot(va, vb) / denom)


class PgVectorStore:
    """Almacén de chunks sobre pgvector con operaciones asíncronas.

    No gestiona sesiones: recibe una `AsyncSession` en cada operación para
    poder participar en transacciones del llamador (endpoints, nodos del grafo).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        chunks: list[Chunk],
        vectors: list[list[float]],
        *,
        document_id: str,
        knowledge_base_id: str,
    ) -> int:
        """Inserta los chunks con sus embeddings.

        Args:
            chunks: chunks ya generados por el chunker.
            vectors: embeddings por chunk (mismo orden).
            document_id: documento de origen (backend `agents.agent_documents`).
            knowledge_base_id: KB a la que pertenece el documento.

        Returns:
            Cantidad de chunks insertados.
        """
        for chunk, vector in zip(chunks, vectors, strict=True):
            self._session.add(
                KnowledgeChunk(
                    document_id=document_id,
                    knowledge_base_id=knowledge_base_id,
                    content=chunk.content,
                    embedding=vector,
                    extra=chunk.metadata,
                    chunk_index=int(chunk.metadata.get("position", 0)),
                )
            )
        await self._session.flush()
        return len(chunks)

    async def search(
        self,
        query_vector: list[float],
        top_k: int,
        *,
        knowledge_base_ids: list[str] | None = None,
        document_id: str | None = None,
    ) -> list[tuple[Chunk, float]]:
        """Búsqueda semántica por similitud coseno (1 - distance = score).

        Args:
            query_vector: embedding de la consulta.
            top_k: máximo de resultados.
            knowledge_base_ids: si se indican, filtra SOLO esas KBs (aislamiento
                entre agentes: un agente nunca recupera chunks de KBs ajenas).
            document_id: filtra un documento concreto (útil para "dónde está esto").

        Returns:
            Lista de (Chunk, score) ordenada por relevancia descendente.
        """
        stmt = select(KnowledgeChunk)
        if knowledge_base_ids:
            stmt = stmt.where(KnowledgeChunk.knowledge_base_id.in_(knowledge_base_ids))
        if document_id:
            stmt = stmt.where(KnowledgeChunk.document_id == document_id)

        rows = (
            await self._session.execute(
                stmt.order_by(KnowledgeChunk.embedding.cosine_distance(query_vector))
                .limit(top_k)
            )
        ).scalars().all()

        results: list[tuple[Chunk, float]] = []
        for row in rows:
            score = _cosine_similarity(query_vector, row.embedding)
            results.append(
                (
                    Chunk(
                        id=str(row.id),
                        content=row.content,
                        metadata=dict(row.extra or {}),
                    ),
                    max(0.0, score),
                )
            )
        return results

    async def delete_document(self, document_id: str) -> int:
        """Elimina todos los chunks de un documento (re-ingest o borrado)."""
        result = await self._session.execute(
            delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id)
        )
        return result.rowcount or 0

    async def count(self, knowledge_base_id: str | None = None) -> int:
        """Total de chunks (opcionalmente filtrado por KB)."""
        stmt = select(func.count(KnowledgeChunk.id))
        if knowledge_base_id:
            stmt = stmt.where(KnowledgeChunk.knowledge_base_id == knowledge_base_id)
        return int((await self._session.execute(stmt)).scalar_one())
