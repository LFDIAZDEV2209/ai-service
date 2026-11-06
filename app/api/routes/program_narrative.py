"""Endpoint interno de narrativa para la asignación del programa."""

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.language_models.chat_models import BaseChatModel

from app.api.security import require_internal_key
from app.core.errors import ConfigError
from app.core.logging import get_logger
from app.schemas.program_narrative import ProgramNarrativeRequest, ProgramNarrativeResponse
from app.services.program_narrative import (
    generate_program_narrative,
    get_program_narrative_model,
)

logger = get_logger(__name__)

router = APIRouter(
    prefix="/internal/program",
    tags=["internal"],
    dependencies=[Depends(require_internal_key)],
)


def get_narrative_model() -> BaseChatModel:
    """Convierte la falta de configuración del proveedor en indisponibilidad."""
    try:
        return get_program_narrative_model()
    except ConfigError as exc:
        logger.error("narrativa 503 detalle", exc_info=True)
        raise HTTPException(status_code=503, detail="Narrativa no disponible") from exc


@router.post("/assignment-narrative", response_model=ProgramNarrativeResponse)
async def assignment_narrative(
    payload: ProgramNarrativeRequest,
    model: BaseChatModel = Depends(get_narrative_model),
) -> ProgramNarrativeResponse:
    """Justifica la propuesta; el backend conserva reintentos y estado del trabajo."""
    try:
        narratives = await generate_program_narrative(payload, model)
    except Exception as exc:
        # Proveedores recientes (Anthropic) rechazan "temperature" como campo
        # extra; reintenta UNA vez sin ella antes de declarar indisponibilidad.
        if "temperature: Extra inputs" in str(exc):
            logger.warning("narrativa reintento sin temperature por incompatibilidad")
            try:
                retry_model = get_program_narrative_model(omit_temperature=True)
                narratives = await generate_program_narrative(payload, retry_model)
            except Exception as retry_exc:
                logger.error("narrativa 503 detalle", exc_info=True)
                raise HTTPException(
                    status_code=503, detail="Narrativa no disponible"
                ) from retry_exc
            return ProgramNarrativeResponse(narratives=narratives)
        logger.error("narrativa 503 detalle", exc_info=True)
        # No exponer datos del paciente ni errores del proveedor en la respuesta.
        raise HTTPException(status_code=503, detail="Narrativa no disponible") from exc
    return ProgramNarrativeResponse(narratives=narratives)
