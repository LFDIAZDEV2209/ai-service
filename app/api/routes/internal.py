"""Endpoints internos protegidos por X-Internal-Key (backend → AI Service)."""

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Base64Bytes, BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runtime_registry import registry as runtime_registry
from app.api.deps import get_db_session
from app.api.security import require_internal_key
from app.db.models import AgentRuntimeConfig
from app.rag.extract import DocumentExtractionError
from app.rag.ingest import DocumentIngestResult, ingest_document_bytes
from app.rag.pgvector_store import PgVectorStore

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[Depends(require_internal_key)],
)


class AgentConfigSyncRequest(BaseModel):
    """Payload de sincronización de la versión activa de un agente."""

    agent_type_id: str = Field(min_length=1)
    version_id: str = Field(min_length=1)
    version_number: int = Field(ge=1)
    name: str = Field(min_length=1)
    description: str | None = None
    specialty: str | None = None
    icon_key: str | None = None
    config: dict[str, Any]


class AgentConfigSyncResponse(BaseModel):
    agent_type_id: str
    version_id: str
    synced: bool = True


class DocumentIngestRequest(BaseModel):
    """Payload de ingestión de un documento (el backend envía el blob)."""

    document_id: str = Field(min_length=1)
    knowledge_base_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    # Bytes del archivo en base64 (el backend lee el blob de su storage).
    # Base64Bytes decodifica el string a bytes al validar.
    content: Base64Bytes


class DocumentIngestResponse(BaseModel):
    document_id: str
    knowledge_base_id: str
    status: str
    chunks_created: int = 0
    replaced_chunks: int = 0
    error: str | None = None


@router.post("/agents/sync-config", response_model=AgentConfigSyncResponse)
async def sync_agent_config(
    payload: AgentConfigSyncRequest,
    session: AsyncSession = Depends(get_db_session),
) -> AgentConfigSyncResponse:
    """Upsert de la configuración de runtime de un tipo de agente.

    El backend es la fuente de verdad del catálogo; este endpoint cachea en
    `ai.agent_runtime_configs` la versión activa para que el runtime compile
    los grafos sin round-trips al backend en cada chat.
    """
    now = datetime.now(UTC)
    existing = (
        await session.execute(
            select(AgentRuntimeConfig).where(
                AgentRuntimeConfig.agent_type_id == payload.agent_type_id
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        session.add(
            AgentRuntimeConfig(
                agent_type_id=payload.agent_type_id,
                version_id=payload.version_id,
                is_active=True,
                config=payload.config,
                synced_at=now,
            )
        )
    else:
        await session.execute(
            update(AgentRuntimeConfig)
            .where(AgentRuntimeConfig.id == existing.id)
            .values(
                version_id=payload.version_id,
                is_active=True,
                config=payload.config,
                synced_at=now,
                updated_at=now,
            )
        )

    await session.commit()

    # Descarta el grafo cacheado: la próxima ejecución recompila con la
    # nueva versión activa.
    runtime_registry.invalidate(payload.agent_type_id)

    return AgentConfigSyncResponse(
        agent_type_id=payload.agent_type_id,
        version_id=payload.version_id,
    )


@router.post("/agents/ingest", response_model=DocumentIngestResponse)
async def ingest_document(
    payload: DocumentIngestRequest,
    session: AsyncSession = Depends(get_db_session),
) -> DocumentIngestResponse:
    """Indexa un documento en pgvector (idempotente por documento).

    El backend llama a este endpoint tras registrar/subir el documento:
    extrae el texto (md/txt/PDF), genera chunks + embeddings y reemplaza los
    chunks anteriores del documento en `ai.knowledge_chunks`.
    """
    try:
        result: DocumentIngestResult = await ingest_document_bytes(
            session=session,
            document_id=payload.document_id,
            knowledge_base_id=payload.knowledge_base_id,
            filename=payload.filename,
            content=payload.content,
        )
    except DocumentExtractionError as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        await session.rollback()
        logger.exception("Error indexando documento %s", payload.document_id)
        raise HTTPException(status_code=500, detail=f"Error indexando documento: {exc}") from exc

    await session.commit()

    return DocumentIngestResponse(
        document_id=result.document_id,
        knowledge_base_id=result.knowledge_base_id,
        status=result.status,
        chunks_created=result.chunks_created,
        replaced_chunks=result.replaced_chunks,
    )


@router.delete("/agents/ingest/{document_id}", response_model=DocumentIngestResponse)
async def delete_document_chunks(
    document_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> DocumentIngestResponse:
    """Elimina los chunks de un documento (borrado del catálogo en el backend)."""
    deleted = await PgVectorStore(session).delete_document(document_id)
    await session.commit()

    return DocumentIngestResponse(
        document_id=document_id,
        knowledge_base_id="",
        status="eliminado",
        chunks_created=0,
        replaced_chunks=deleted,
    )
