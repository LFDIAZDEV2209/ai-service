"""Tests de extracción de texto e ingestión RAG (sin BD ni API keys)."""

from __future__ import annotations

import pytest

from app.rag.extract import DocumentExtractionError, extract_text
from app.rag.ingest import ingest_document_bytes


def test_extract_markdown():
    text = extract_text("guia.md", "# Título\n\nContenido".encode())
    assert "Título" in text


def test_extract_txt_latin1_fallback():
    raw = "caf\xe9".encode("latin-1")
    text = extract_text("notas.txt", raw)
    assert "caf" in text


def test_extract_unsupported_extension():
    with pytest.raises(DocumentExtractionError, match="no soportado"):
        extract_text("data.zip", b"blob")


def test_extract_pdf_empty_raises():
    # PDF sin páginas extraíbles (bytes inválidos) → error controlado.
    with pytest.raises(DocumentExtractionError):
        extract_text("doc.pdf", b"not a real pdf")


class _FakeEmbeddings:
    """Embeddings deterministas de 1536 dims para el test de ingest."""

    dimensions = 1536

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] * self.dimensions for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0] * self.dimensions


class _FakeStore:
    def __init__(self):
        self.added: list[tuple[str, int]] = []
        self.deleted: list[str] = []

    async def delete_document(self, document_id: str) -> int:
        self.deleted.append(document_id)
        return 3

    async def add(self, chunks, vectors, *, document_id: str, knowledge_base_id: str) -> int:
        self.added.append((document_id, len(chunks)))
        return len(chunks)


class _FakeSession:
    async def flush(self):
        pass


async def test_ingest_document_bytes_replaces_chunks():
    store = _FakeStore()
    session = _FakeSession()

    # Reemplazamos el PgVectorStore por el fake via monkeypatch manual.
    import app.rag.ingest as ingest_mod

    original = ingest_mod.PgVectorStore
    ingest_mod.PgVectorStore = lambda _session: store  # type: ignore[assignment]
    try:
        result = await ingest_document_bytes(
            session=session,
            document_id="doc-1",
            knowledge_base_id="kb-1",
            filename="manual.md",
            content="# Instalación\n\nPara instalar: pip install coppai.\n\n"
            "# Uso\n\nContenido de uso.\n".encode(),
            embeddings=_FakeEmbeddings(),
        )
    finally:
        ingest_mod.PgVectorStore = original

    assert result.status == "indexado"
    assert result.chunks_created >= 2
    assert result.replaced_chunks == 3
    assert store.deleted == ["doc-1"]
    assert store.added[0][0] == "doc-1"


async def test_ingest_empty_document_raises():
    with pytest.raises(DocumentExtractionError, match="vacío"):
        await ingest_document_bytes(
            session=_FakeSession(),
            document_id="doc-2",
            knowledge_base_id="kb-1",
            filename="vacio.md",
            content=b"   \n  ",
            embeddings=_FakeEmbeddings(),
        )
