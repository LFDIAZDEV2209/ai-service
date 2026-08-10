"""Tests del pipeline RAG (con embeddings hash deterministas, sin API keys)."""

from pathlib import Path
from tempfile import TemporaryDirectory

from app.rag.chunker import chunk_markdown
from app.rag.embeddings import HashEmbeddingsProvider
from app.rag.ingest import ingest_directory
from app.rag.retriever import Retriever
from app.rag.vector_store import InMemoryVectorStore


def test_chunker_splits_by_headings():
    text = (
        "# Intro\n\nTexto de introducción.\n\n"
        "## Instalación\n\nContenido de instalación.\n\n"
        "## Uso\n\nContenido de uso.\n"
    )
    chunks = chunk_markdown(text, source="doc.md")
    headings = {c.metadata["heading"] for c in chunks}
    assert {"Intro", "Instalación", "Uso"} <= headings
    assert all(c.metadata["source"] == "doc.md" for c in chunks)


def test_embeddings_are_deterministic():
    emb = HashEmbeddingsProvider()
    assert emb.embed_query("hola mundo") == emb.embed_query("hola mundo")
    assert len(emb.embed_query("hola")) == emb.dimensions


def test_ingest_and_retrieve():
    with TemporaryDirectory() as tmp:
        doc = Path(tmp) / "guia.md"
        doc.write_text(
            "# Instalación\n\nPara instalar ejecuta: `pip install coppai`.\n\n"
            "# Configuración\n\nOtro contenido que no responde a la pregunta.\n",
            encoding="utf-8",
        )
        embeddings = HashEmbeddingsProvider()
        store = InMemoryVectorStore()

        stats = ingest_directory(tmp, store=store, embeddings=embeddings)
        assert stats.files_ingested == 1
        assert stats.chunks_created >= 2
        assert stats.errors == []

        retriever = Retriever(store=store, embeddings=embeddings)
        results = retriever.retrieve("instalar coppai", top_k=3)
        assert results
        top_content = results[0].chunk.content.lower()
        assert "instalar" in top_content


def test_ingest_missing_directory_raises():
    import pytest

    with pytest.raises(FileNotFoundError):
        ingest_directory("/no/existe/esto")
