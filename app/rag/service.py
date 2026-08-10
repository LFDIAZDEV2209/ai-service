"""Singleton del pipeline RAG para la API y el subgrafo.

Advertencia: el store es en memoria (no se comparte entre workers ni
persiste entre reinicios). Para producción migrar a un store compartido
(pgvector / Chroma / Qdrant) — ver app/rag/vector_store.py.
"""

from __future__ import annotations

import threading

from app.rag.embeddings import get_embeddings
from app.rag.retriever import Retriever
from app.rag.vector_store import InMemoryVectorStore

_lock = threading.Lock()
_retriever: Retriever | None = None


def get_retriever() -> Retriever:
    global _retriever
    with _lock:
        if _retriever is None:
            _retriever = Retriever(
                store=InMemoryVectorStore(),
                embeddings=get_embeddings(),
            )
        return _retriever


def reset_retriever() -> None:
    global _retriever
    with _lock:
        _retriever = None
