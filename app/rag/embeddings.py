"""Proveedores de embeddings.

Interfaz `EmbeddingsProvider` + dos implementaciones:
- `OpenAIEmbeddingsProvider`: producción (text-embedding-3-small por defecto).
- `HashEmbeddingsProvider`: desarrollo/pruebas sin API key (determinista).

Para escala real se puede conectar un servicio de embeddings dedicado o pgvector.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Protocol

logger = logging.getLogger(__name__)


class EmbeddingsProvider(Protocol):
    """Contrato de un proveedor de embeddings."""

    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class OpenAIEmbeddingsProvider:
    """Embeddings de OpenAI (langchain-openai). Requiere OPENAI_API_KEY."""

    def __init__(self, model: str | None = None):
        from app.core.config import get_settings

        settings = get_settings()
        if not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY no configurada. Usa HashEmbeddingsProvider para desarrollo."
            )
        from langchain_openai import OpenAIEmbeddings

        self._embeddings = OpenAIEmbeddings(
            model=model or settings.openai_embedding_model,
            api_key=settings.openai_api_key,
        )
        self.dimensions: int = 1536

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embeddings.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embeddings.embed_query(text)


class HashEmbeddingsProvider:
    """Embeddings deterministas sin API key — SOLO desarrollo y tests.

    Basados en hashing de tokens. No aptos para producción, pero permiten
    probar el pipeline RAG completo offline.
    """

    def __init__(self, dimensions: int = 256):
        self.dimensions = dimensions
        self._vocab: dict[str, int] = {}
        self._next_index = 0

    def _tokenize(self, text: str) -> list[str]:
        return [t for t in re.split(r"[^a-z0-9áéíóúñü]+", text.lower()) if t]

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in self._tokenize(text):
            index = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % self.dimensions
            vector[index] += 1.0
        norm = sum(v * v for v in vector) ** 0.5 or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


def get_embeddings(provider: str = "auto") -> EmbeddingsProvider:
    """Fábrica de embeddings: 'auto' usa OpenAI si hay key, si no el hash provider."""
    from app.core.config import get_settings

    settings = get_settings()
    if provider in {"hash", "dev"} or (provider == "auto" and not settings.openai_api_key):
        logger.warning("Usando HashEmbeddingsProvider (solo desarrollo).")
        return HashEmbeddingsProvider()
    return OpenAIEmbeddingsProvider()
