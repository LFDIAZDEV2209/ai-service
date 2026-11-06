"""Redacción estructurada; el motor C# sigue siendo la autoridad nutricional."""

import asyncio
import logging

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.errors import ConfigError
from app.llm.factory import get_chat_model
from app.schemas.nutrition_draft import (
    BilingualText,
    NutritionDraftContent,
    NutritionDraftRequest,
    NutritionDraftResponse,
)

logger = logging.getLogger(__name__)


def get_nutrition_model() -> BaseChatModel | None:
    """Reutiliza Anthropic; ausencia de configuración habilita el fallback previsto."""
    try:
        # Los modelos Anthropic actuales rechazan temperature: no enviarlo.
        return get_chat_model(provider="anthropic", max_tokens=8192).model_copy(
            update={"temperature": None}
        )
    except ConfigError:
        return None


def deterministic_draft(payload: NutritionDraftRequest) -> NutritionDraftResponse:
    """Devuelve la selección del motor, sin inferir datos clínicos ausentes."""
    return NutritionDraftResponse(
        narratives=BilingualText(
            es="Propuesta determinista pendiente de revisión individual por nutrición.",
            en="Deterministic proposal pending individual nutrition review.",
        ),
        instructions=BilingualText(
            es="Conservar las porciones calculadas y revisar las alertas antes de aprobar.",
            en="Keep the calculated portions and review the alerts before approval.",
        ),
        menus=[m.model_copy(deep=True) for m in payload.menus],
        source="deterministic_fallback",
    )


async def generate_nutrition_draft(
    payload: NutritionDraftRequest, model: BaseChatModel | None
) -> NutritionDraftResponse:
    if model is None:
        return deterministic_draft(payload)
    try:
        runnable = model.with_structured_output(NutritionDraftContent.model_json_schema())
        result = await asyncio.wait_for(
            runnable.ainvoke(
                [
                    SystemMessage(
                        content=(
                            "Redacta narrativa e indicaciones en español e inglés. "
                            "Solo usa alimentos "
                            "del catálogo y conserva todas las claves de menú. No inventes datos "
                            "clínicos ni modifiques metas. "
                            "Los menús serán revalidados por el motor. "
                            "Nunca afirmes que el plan ya fue revisado o aprobado."
                            " El perfil medido y las reglas aplicadas explican la propuesta; "
                            "respeta su procedencia y faltantes. No deduzcas glicemia ni grasa "
                            "desde IMC/HbA1c ni conviertas una ausencia en un valor normal."
                        )
                    ),
                    HumanMessage(content=payload.model_dump_json()),
                ]
            ),
            timeout=45,
        )
        content = NutritionDraftContent.model_validate(result)
        if len(content.menus) != len(payload.menus) or {m.key for m in content.menus} != {
            m.key for m in payload.menus
        }:
            raise ValueError("Selección de menús inválida")
        if any(i.food_id not in payload.catalog_ids for m in content.menus for i in m.items):
            raise ValueError("Alimento ajeno al catálogo")
        return NutritionDraftResponse(**content.model_dump(), source="llm")
    except Exception:
        # No registrar el payload ni la excepción: podrían contener datos sensibles.
        logger.warning("nutrition_draft_fallback")
        return deterministic_draft(payload)
