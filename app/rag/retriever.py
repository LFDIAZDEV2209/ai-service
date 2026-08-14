"""Recuperador RAG: query → embeddings → top-k chunks.

- `Retriever`: síncrono sobre stores en memoria (desarrollo/tests).
- `PgVectorRetriever`: asíncrono sobre pgvector con filtrado por metadata
  (knowledge_base_ids/document_id) — el camino de producción, aislado por KB
  para que un agente nunca recupere chunks de conocimiento ajeno.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.rag.chunker import Chunk
from app.rag.embeddings import EmbeddingsProvider, get_embeddings
from app.rag.pgvector_store import PgVectorStore
from app.rag.vector_store import InMemoryVectorStore, VectorStore


@dataclass
class RetrievedChunk:
    chunk: Chunk
    score: float


class Retriever:
    def __init__(
        self,
        store: VectorStore | None = None,
        embeddings: EmbeddingsProvider | None = None,
    ):
        self.store: VectorStore = store or InMemoryVectorStore()
        self.embeddings: EmbeddingsProvider = embeddings or get_embeddings()

    def retrieve(self, query: str, top_k: int | None = None) -> list[RetrievedChunk]:
        settings = get_settings()
        top_k = top_k or settings.rag_top_k
        query_vector = self.embeddings.embed_query(query)
        results = self.store.search(query_vector, top_k)
        return [RetrievedChunk(chunk=chunk, score=score) for chunk, score in results]

    def format_context(self, results: list[RetrievedChunk], max_chars: int = 4000) -> str:
        """Convierte los chunks en un bloque de contexto citable para el LLM."""
        parts: list[str] = []
        used = 0
        for item in results:
            if used >= max_chars:
                break
            block = (
                f"[Fuente: {item.chunk.metadata.get('source', '?')} — "
                f"Sección: {item.chunk.metadata.get('heading', '?')}]\n"
                f"{item.chunk.content}"
            )
            parts.append(block)
            used += len(block)
        return "\n\n".join(parts)


class PgVectorRetriever:
    """Recuperador asíncrono sobre pgvector con filtrado por metadata.

    El filtro por `knowledge_base_ids` es obligatorio en la práctica: garantiza
    que un agente solo recupere chunks de sus KBs (aislamiento entre agentes).
    """

    def __init__(self, embeddings: EmbeddingsProvider | None = None):
        self.embeddings: EmbeddingsProvider = embeddings or get_embeddings()

    async def retrieve(
        self,
        session: AsyncSession,
        query: str,
        *,
        knowledge_base_ids: list[str] | None = None,
        document_id: str | None = None,
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        settings = get_settings()
        top_k = top_k or settings.rag_top_k
        query_vector = self.embeddings.embed_query(query)
        results = await PgVectorStore(session).search(
            query_vector,
            top_k,
            knowledge_base_ids=knowledge_base_ids,
            document_id=document_id,
        )
        return [RetrievedChunk(chunk=chunk, score=score) for chunk, score in results]

    def format_context(self, results: list[RetrievedChunk], max_chars: int = 4000) -> str:
        """Reutiliza el mismo formato citable que `Retriever.format_context`."""
        parts: list[str] = []
        used = 0
        for item in results:
            if used >= max_chars:
                break
            block = (
                f"[Fuente: {item.chunk.metadata.get('source', '?')} — "
                f"Sección: {item.chunk.metadata.get('heading', '?')}]\n"
                f"{item.chunk.content}"
            )
            parts.append(block)
            used += len(block)
        return "\n\n".join(parts)
