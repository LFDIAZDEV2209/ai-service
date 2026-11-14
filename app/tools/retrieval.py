"""Tool de recuperación RAG para agentes (conocimiento oficial).

Se crea por agente vía `make_retrieve_tool`: el runtime inyecta las KBs
configuradas (`retrieval_config.knowledge_base_ids`) y el `top_k`, de modo que
la tool nunca recupera conocimiento ajeno al agente (aislamiento por KB).

El resultado es un bloque de contexto citable ([Fuente: ...]) que el LLM puede
usar para responder; las fuentes quedan visibles en el mensaje de la tool para
el playground/observabilidad.
"""

from __future__ import annotations

import json
import logging
from typing import cast

from langchain_core.tools import BaseTool, tool

from app.db.engine import async_session
from app.rag.retriever import PgVectorRetriever

logger = logging.getLogger(__name__)

RETRIEVE_TOOL_NAME = "retrieve_knowledge"

RETRIEVE_TOOL_DESCRIPTION = (
    "Search the authorized official documentation, protocols and guidelines / "
    "Busca documentación, protocolos y guías oficiales autorizadas. "
    "Use for domain questions and requests about uploaded material, in English or Spanish, "
    "before answering from general knowledge. Returns source-cited excerpts, not patient data."
)


def make_retrieve_tool(
    knowledge_base_ids: list[str] | None = None,
    top_k: int = 5,
):
    """Crea la tool `retrieve_knowledge` para un agente concreto.

    Args:
        knowledge_base_ids: KBs a las que el agente puede consultar. Vacío/None
            → la tool devuelve vacío (el agente no tiene conocimiento asignado).
        top_k: máximo de chunks recuperados.
    """
    kb_ids = list(knowledge_base_ids or [])

    @tool
    async def retrieve_knowledge(query: str) -> str:
        """Consulta el conocimiento oficial del agente y devuelve fragmentos citados."""
        if not kb_ids:
            return (
                "Este agente no tiene fuentes de conocimiento configuradas. "
                "Responde con tu conocimiento general y dilo claramente."
            )

        retriever = PgVectorRetriever()
        async with async_session() as session:
            results = await retriever.retrieve(
                session,
                query,
                knowledge_base_ids=kb_ids,
                top_k=top_k,
            )

        if not results:
            return (
                "DOCUMENTATION STATUS: no relevant excerpts were retrieved from the authorized "
                "active sources. Do not claim the documents say anything or that personal data "
                "is unavailable. State this precise limitation in the user's language; clearly "
                "label any useful general education as general information."
            )

        context = retriever.format_context(results, max_chars=3000)
        sources = [
            {
                "source": r.chunk.metadata.get("source", "?"),
                "heading": r.chunk.metadata.get("heading", "?"),
                "score": round(r.score, 4),
            }
            for r in results
        ]
        # Las fuentes se exponen en un bloque JSON aparte para observabilidad
        # (el playground las muestra); el contexto citado es lo que el LLM usa.
        return f"{context}\n\n[FUENTES: {json.dumps(sources, ensure_ascii=False)}]"

    tool_instance = cast(BaseTool, retrieve_knowledge)
    tool_instance.name = RETRIEVE_TOOL_NAME
    tool_instance.description = RETRIEVE_TOOL_DESCRIPTION
    return tool_instance
