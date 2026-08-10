"""Ingestión de documentación: directorio → chunks → embeddings → vector store."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.rag.chunker import Chunk, chunk_markdown
from app.rag.embeddings import EmbeddingsProvider, get_embeddings
from app.rag.vector_store import InMemoryVectorStore, VectorStore

_SUPPORTED_EXTENSIONS = {".md", ".markdown", ".txt"}


@dataclass
class IngestStats:
    files_scanned: int
    files_ingested: int
    chunks_created: int
    errors: list[str]


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
