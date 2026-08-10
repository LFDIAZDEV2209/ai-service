"""Almacén vectorial.

Interfaz `VectorStore` + implementación en memoria (numpy, coseno).

Para producción se recomienda cambiar a una implementación con:
- SQLite + sqlite-vec (como el curso, cero infraestructura), o
- pgvector (Postgres que ya usarás para el checkpointer), o
- Chroma / Qdrant / Pinecone según escala.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np

from app.rag.chunker import Chunk


class VectorStore(Protocol):
    def add(self, chunks: list[Chunk], vectors: list[list[float]]) -> None: ...

    def search(self, query_vector: list[float], top_k: int) -> list[tuple[Chunk, float]]: ...

    def clear(self) -> None: ...

    def count(self) -> int: ...


class InMemoryVectorStore:
    """Implementación en memoria con similitud coseno. Suficiente para desarrollo."""

    def __init__(self) -> None:
        self._items: list[tuple[Chunk, np.ndarray]] = []

    def add(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        for chunk, vector in zip(chunks, vectors, strict=True):
            self._items.append((chunk, np.asarray(vector, dtype=np.float64)))

    def search(self, query_vector: list[float], top_k: int) -> list[tuple[Chunk, float]]:
        q = np.asarray(query_vector, dtype=np.float64)
        scored: list[tuple[Chunk, float]] = []
        for chunk, vector in self._items:
            dot = float(np.dot(q, vector))
            denom = float(np.linalg.norm(q) * np.linalg.norm(vector))
            score = dot / denom if denom > 0 else 0.0
            scored.append((chunk, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]

    def clear(self) -> None:
        self._items = []

    def count(self) -> int:
        return len(self._items)
