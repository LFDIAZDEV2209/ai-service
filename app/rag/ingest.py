"""Ingestión de documentación: contenido → chunks → embeddings → vector store.

Dos caminos:
- `ingest_directory`: utilitario de desarrollo (md/txt del filesystem → store
  en memoria). Mantiene los tests offline sin BD.
- `ingest_document_bytes`: producción (archivo del backend → pgvector). Es
  idempotente por documento: reemplaza los chunks existentes del documento.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.chunker import Chunk, chunk_markdown
from app.rag.embeddings import EmbeddingsProvider, get_embeddings
from app.rag.extract import DocumentExtractionError, extract_text
from app.rag.pgvector_store import PgVectorStore
from app.rag.vector_store import InMemoryVectorStore, VectorStore

logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = {".md", ".markdown", ".txt"}


@dataclass
class IngestStats:
    files_scanned: int
    files_ingested: int
    chunks_created: int
    errors: list[str]


@dataclass
class DocumentIngestResult:
    """Resultado de la ingestión de un documento en pgvector."""

    document_id: str
    knowledge_base_id: str
    chunks_created: int
    replaced_chunks: int
    status: str  # "indexado" | "error"
    error: str | None = None


async def ingest_document_bytes(
    *,
    session: AsyncSession,
    document_id: str,
    knowledge_base_id: str,
    filename: str,
    content: bytes,
    embeddings: EmbeddingsProvider | None = None,
    chunk_size: int = 1500,
    batch_size: int = 16,
) -> DocumentIngestResult:
    """Indexa un documento en pgvector (reemplazo idempotente por documento).

    Flujo: extracción de texto (md/txt/PDF) → chunking por encabezados →
    embeddings por lotes → reemplazo de chunks del documento en
    `ai.knowledge_chunks` (borra los viejos e inserta los nuevos, todo en la
    misma transacción del llamador).

    Raises:
        DocumentExtractionError: si el archivo no se puede parsear.
    """
    text = extract_text(filename, content)
    if not text.strip():
        raise DocumentExtractionError(f"El documento {filename} está vacío.")

    chunks: list[Chunk] = chunk_markdown(
        text,
        source=filename,
        max_chars=chunk_size,
    )
    if not chunks:
        raise DocumentExtractionError(f"El documento {filename} no generó chunks.")

    embeddings = embeddings or get_embeddings()
    store = PgVectorStore(session)

    replaced = await store.delete_document(document_id)

    created = 0
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        vectors = embeddings.embed_documents([c.content for c in batch])
        created += await store.add(
            batch,
            vectors,
            document_id=document_id,
            knowledge_base_id=knowledge_base_id,
        )

    logger.info(
        "Documento indexado: %s (%d chunks, reemplazados %d)",
        document_id,
        created,
        replaced,
    )
    return DocumentIngestResult(
        document_id=document_id,
        knowledge_base_id=knowledge_base_id,
        chunks_created=created,
        replaced_chunks=replaced,
        status="indexado",
    )


def ingest_directory(
    path: str | Path,
    store: VectorStore | None = None,
    embeddings: EmbeddingsProvider | None = None,
    chunk_size: int = 1500,
    batch_size: int = 16,
) -> IngestStats:
    """Indexa todos los documentos `.md/.txt` de un directorio (recursivo)."""
    docs_dir = Path(path)
    if not docs_dir.exists():
        raise FileNotFoundError(f"Ruta de documentación no existe: {docs_dir}")

    store = store or InMemoryVectorStore()
    embeddings = embeddings or get_embeddings()
    stats = IngestStats(files_scanned=0, files_ingested=0, chunks_created=0, errors=[])

    files = [
        p for p in docs_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in _SUPPORTED_EXTENSIONS
    ]
    stats.files_scanned = len(files)

    for file_path in files:
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
            chunks: list[Chunk] = chunk_markdown(
                text,
                source=str(file_path.relative_to(docs_dir.parent)),
                max_chars=chunk_size,
            )
            # embeddings por lotes
            for i in range(0, len(chunks), batch_size):
                batch = chunks[i : i + batch_size]
                vectors = embeddings.embed_documents([c.content for c in batch])
                store.add(batch, vectors)
            stats.chunks_created += len(chunks)
            stats.files_ingested += 1
        except Exception as exc:
            stats.errors.append(f"{file_path}: {exc}")

    return stats
