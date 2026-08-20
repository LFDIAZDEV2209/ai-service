"""Ingestión de documentación para el RAG.

Utilidad de desarrollo restringida al canal interno (X-Internal-Key): el
flujo de producción indexa documentos vía `/internal/agents/ingest` (blobs).
Este endpoint acepta un directorio local, limitado al root configurado en
`DOCS_PATH`, sin traversal ni rutas absolutas arbitrarias.
"""

import os

from fastapi import APIRouter, Depends, HTTPException

from app.api.schemas import IngestRequest, IngestResponse
from app.api.security import require_internal_key
from app.core.config import get_settings
from app.core.logging import get_logger
from app.rag.ingest import ingest_directory
from app.rag.service import get_retriever

logger = get_logger(__name__)

router = APIRouter(
    prefix="/ingest",
    tags=["ingest"],
    dependencies=[Depends(require_internal_key)],
)


def _validate_ingest_path(raw_path: str) -> str:
    """Valida que el path quede dentro del directorio permitido.

    Rechaza:
    - Paths absolutos arbitrarios (solo se permiten subrutas de `DOCS_PATH`).
    - Traversal (`..`, `~`, segmentos vacíos abusivos).
    - Directorios fuera del root configurado.
    """
    root = os.path.abspath(get_settings().docs_path)

    if not raw_path or os.path.isabs(raw_path):
        raise HTTPException(
            status_code=400,
            detail="Solo se permiten rutas relativas dentro del directorio de documentos.",
        )

    norm = os.path.normpath(raw_path)
    parts = norm.split(os.sep)
    if any(part in {"..", "~"} for part in parts) or norm.startswith(".."):
        raise HTTPException(status_code=400, detail="Ruta inválida: traversal no permitido.")

    target = os.path.abspath(os.path.join(root, norm))
    if target != root and not target.startswith(root + os.sep):
        raise HTTPException(status_code=400, detail="Ruta fuera del directorio permitido.")

    if not os.path.isdir(target):
        raise HTTPException(status_code=400, detail=f"Directorio no encontrado: {norm}")

    return target


@router.post("", response_model=IngestResponse)
async def ingest_docs(request: IngestRequest) -> IngestResponse:
    """Indexa los documentos `.md/.txt` de un directorio (dentro de DOCS_PATH)
    en el vector store."""
    target = _validate_ingest_path(request.path)

    retriever = get_retriever()
    try:
        stats = ingest_directory(
            target,
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
