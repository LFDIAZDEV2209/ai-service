"""Ingestión de documentación para el RAG."""

from fastapi import APIRouter, HTTPException

from app.api.schemas import IngestRequest, IngestResponse
from app.core.logging import get_logger
from app.rag.ingest import ingest_directory
from app.rag.service import get_retriever

logger = get_logger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("", response_model=IngestResponse)
async def ingest_docs(request: IngestRequest) -> IngestResponse:
    """Indexa los documentos `.md/.txt` de un directorio en el vector store."""
    retriever = get_retriever()
    try:
        stats = ingest_directory(
            request.path,
            store=retriever.store,
            embeddings=retriever.embeddings,
            chunk_size=request.chunk_size,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info(
        "Ingestión completada: %s archivos, %s chunks",
        stats.files_ingested,
        stats.chunks_created,
    )
    return IngestResponse(
        files_scanned=stats.files_scanned,
        files_ingested=stats.files_ingested,
        chunks_created=stats.chunks_created,
        errors=stats.errors,
    )
