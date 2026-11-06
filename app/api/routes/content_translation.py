"""Traducción interna de texto publicado, sin herramientas ni mutaciones clínicas."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.security import require_internal_key
from app.schemas.content_translation import ContentTranslationRequest, ContentTranslationResponse
from app.services.content_translation import (
    ContentTranslationService,
    TranslationRejectedError,
    get_content_translation_service,
)

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/internal/content", tags=["internal"], dependencies=[Depends(require_internal_key)]
)


@router.post("/translate", response_model=ContentTranslationResponse)
async def translate_content(
    payload: ContentTranslationRequest,
    service: ContentTranslationService = Depends(get_content_translation_service),
) -> ContentTranslationResponse:
    try:
        return await service.translate(payload)
    except TranslationRejectedError as exc:
        # No registrar texto, identificadores, respuesta del modelo ni excepción completa.
        logger.warning("Traducción rechazada: exception_type=%s", type(exc).__name__)
        raise HTTPException(status_code=422, detail="Translation output rejected") from None
    except Exception as exc:
        logger.warning("Traducción no disponible: exception_type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Translation unavailable") from None
