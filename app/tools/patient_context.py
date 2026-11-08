"""Consulta de solo lectura del contexto del paciente autenticado del turno."""

import asyncio
import json

import httpx
from langchain_core.tools import tool
from langgraph.config import get_config

from app.core.config import get_settings

NO_PATIENT_DATA = "Sin datos disponibles del paciente en este momento."


@tool
async def get_patient_data() -> str:
    """Consulta medidas, plan nutricional y métricas recientes del paciente actual.

    Usa esta herramienta cuando necesites datos reales del paciente para responder.
    Si no hay datos disponibles, no inventes valores.
    """
    try:
        # La identidad viene del backend, nunca de argumentos elegidos por el modelo.
        user_id = get_config().get("configurable", {}).get("user_id")
        if not user_id:
            return NO_PATIENT_DATA
        settings = get_settings()
        if not settings.backend_base_url or not settings.internal_api_key:
            return NO_PATIENT_DATA

        # El límite total también cubre conexiones lentas y lecturas parciales.
        async with asyncio.timeout(8), httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(
                f"{settings.backend_base_url.rstrip('/')}/api/v1/internal/agents/patient-context",
                params={"userId": str(user_id)},
                headers={"X-Internal-Key": settings.internal_api_key},
            )
            if response.status_code != 200:
                return NO_PATIENT_DATA
            data = response.json()
            return json.dumps(data, ensure_ascii=False) if data is not None else NO_PATIENT_DATA
    except Exception:
        # No registrar datos clínicos ni credenciales; cualquier fallo degrada sin romper el chat.
        return NO_PATIENT_DATA
