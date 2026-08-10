"""Healthcheck del servicio."""

from fastapi import APIRouter

from app.api.schemas import HealthResponse
from app.core.config import get_settings
from app.tools.registry import list_tool_names

router = APIRouter(tags=["health"])

VERSION = "0.1.0"


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        environment=settings.environment,
        provider=settings.llm_provider,
        model=(
            settings.anthropic_model
            if settings.llm_provider == "anthropic"
            else settings.openai_model
        ),
        tools=list_tool_names(),
        version=VERSION,
    )
