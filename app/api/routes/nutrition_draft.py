"""Endpoint interno de propuestas nutricionales, autenticado y sin acceso a BD."""

from fastapi import APIRouter, Depends
from langchain_core.language_models.chat_models import BaseChatModel

from app.api.security import require_internal_key
from app.schemas.nutrition_draft import NutritionDraftRequest, NutritionDraftResponse
from app.services.nutrition_draft import generate_nutrition_draft, get_nutrition_model

router = APIRouter(
    prefix="/internal/nutrition",
    tags=["internal"],
    dependencies=[Depends(require_internal_key)],
)


@router.post("/plan-draft", response_model=NutritionDraftResponse)
async def plan_draft(
    payload: NutritionDraftRequest,
    model: BaseChatModel | None = Depends(get_nutrition_model),
) -> NutritionDraftResponse:
    """La identidad del paciente permanece en el backend; aquí no se acepta actor."""
    return await generate_nutrition_draft(payload, model)
