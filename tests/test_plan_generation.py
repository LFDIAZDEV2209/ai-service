"""Tests del endpoint interno de generación de planes (wellness) con IA.

Patrón del repo: LLM falso (`FakeToolAwareModel`) sin API keys, pytest-asyncio
en modo auto, y `app.dependency_overrides` para el servicio en el router.
"""

import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.api.main import create_app
from app.core.config import get_settings
from app.schemas.plan_generation import (
    ExerciseRoutineOut,
    NutritionPlanOut,
    PlanGenerationRequest,
    PlanGenerationResponse,
)
from app.services.plan_generation import (
    _extract_json,
    generate_plan,
    get_plan_generation_service,
)
from tests.fakes import FakeToolAwareModel

INTERNAL_KEY = get_settings().internal_api_key
HEADERS = {"X-Internal-Key": INTERNAL_KEY}

NUTRITION_PLAN = {
    "name": "Plan personalizado María",
    "description": "Plan bajo en azúcares para controlar la glucemia",
    "target_condition": "Prediabetes",
    "duration_days": 7,
    "daily_calorie_target": 1800,
    "daily_protein_target": 120,
    "daily_carbs_target": 200,
    "daily_fat_target": 60,
    "daily_fiber_target": 30,
    "allergens": "Ninguno",
    "meal_timing": "7:00, 12:00, 15:30, 19:00",
    "days": [
        {
            "day_number": 1,
            "meal_type": "Desayuno",
            "description": "Desayuno balanceado",
            "foods": "Avena integral, huevos, fruta",
            "calories": 380,
            "protein_g": 18,
            "carbs_g": 52,
            "fat_g": 10,
            "fiber_g": 6,
            "water_ml": 250,
            "notes": "Sin azúcar añadida",
        }
    ],
}

EXERCISE_PLAN = {
    "name": "Rutina personalizada María",
    "description": "Rutina mixta de bajo impacto",
    "difficulty": "Moderado",
    "estimated_minutes": 30,
    "category": "Mixta",
    "target_muscles": "Piernas, Core",
    "equipment": "Banda elástica",
    "warmup_notes": "5 min de movilidad articular",
    "cooldown_notes": "5 min de estiramientos",
    "exercises": [
        {
            "name": "Sentadilla asistida",
            "description": "Sentadilla con apoyo en silla",
            "sets": 3,
            "repetitions": 12,
            "rest_seconds": 60,
            "duration_secs": None,
            "weight_kg": None,
            "target_muscle": "Cuádriceps",
            "equipment": "Banda elástica",
            "tempo": "2-1-2",
            "rpe": 6,
            "tips": "Mantener la espalda recta",
        }
    ],
}


def request_body(plan_type: str = "nutrition") -> dict:
    """Payload de ejemplo del contrato (backend .NET → AI Service)."""
    return {
        "type": plan_type,
        "clinical_context": {
            "patient": {"age": 38, "gender": "F", "exercise_level": "Bajo"},
            "measurements": [
                {
                    "metric": "glucose_fasting",
                    "value": 110,
                    "unit": "mg_dl",
                    "observed_at": "2026-08-20T10:00:00Z",
                }
            ],
            "allergies": ["Penicilina"],
            "diagnoses": ["E66.01 Obesidad leve"],
            "medications": ["Metformina 500mg"],
            "lifestyle": {"smoking": "No", "alcohol": "Ocasional", "exercise_level": "Bajo"},
        },
        "restrictions": [
            {
                "rule": "Evitar azúcares añadidos y carbohidratos de alto índice glucémico",
                "severity": "block",
            }
        ],
    }


# ── Esquemas ────────────────────────────────────────────────────────────────


def test_request_schema_validates_contract_sample():
    request = PlanGenerationRequest.model_validate(request_body())
    assert request.type == "nutrition"
    assert request.clinical_context.patient.age == 38
    assert request.clinical_context.measurements[0].metric == "glucose_fasting"
    assert request.restrictions[0].severity == "block"


def test_nutrition_plan_out_validates_contract_sample():
    plan = NutritionPlanOut.model_validate(NUTRITION_PLAN)
    assert plan.duration_days == 7
    assert plan.daily_calorie_target == 1800
    assert plan.days[0].foods == "Avena integral, huevos, fruta"


def test_exercise_routine_out_validates_with_nullable_fields():
    plan = ExerciseRoutineOut.model_validate(EXERCISE_PLAN)
    assert plan.difficulty == "Moderado"
    assert plan.exercises[0].duration_secs is None
    assert plan.exercises[0].weight_kg is None
    assert plan.exercises[0].tempo == "2-1-2"


# ── Utilidades internas ─────────────────────────────────────────────────────


def test_extract_json_with_markdown_fences():
    text = '```json\n{"type": "nutrition", "plan": {"name": "x"}}\n```'
    assert _extract_json(text) == {"type": "nutrition", "plan": {"name": "x"}}


def test_extract_json_with_plain_object():
    text = 'Esto es contexto. {"plan": {"name": "x"}} fin.'
    assert _extract_json(text) == {"plan": {"name": "x"}}


# ── Servicio ────────────────────────────────────────────────────────────────


async def test_generate_plan_nutrition_success():
    fake = FakeToolAwareModel(
        responses=[AIMessage(content=json.dumps({"type": "nutrition", "plan": NUTRITION_PLAN}))]
    )
    result = await generate_plan(PlanGenerationRequest.model_validate(request_body()), model=fake)

    assert isinstance(result, PlanGenerationResponse)
    assert result.type == "nutrition"
    assert result.plan["name"] == "Plan personalizado María"
    assert result.plan["days"][0]["day_number"] == 1
    # La nota "Sin azúcar añadida" no debe disparar el chequeo block.
    assert result.plan["days"][0]["notes"] == "Sin azúcar añadida"


async def test_generate_plan_exercise_success():
    fake = FakeToolAwareModel(
        responses=[AIMessage(content=json.dumps({"type": "exercise", "plan": EXERCISE_PLAN}))]
    )
    result = await generate_plan(
        PlanGenerationRequest.model_validate(request_body("exercise")), model=fake
    )

    assert result.type == "exercise"
    assert result.plan["exercises"][0]["name"] == "Sentadilla asistida"
    assert result.plan["exercises"][0]["rpe"] == 6


async def test_generate_plan_raises_422_when_model_fails():
    # Con structured output el proveedor garantiza el esquema: si falla
    # repetidamente, se propaga como 422.
    fake = FakeToolAwareModel(
        responses=[
            AIMessage(content="esto no es JSON"),
            AIMessage(content="tampoco"),
        ]
    )
    with pytest.raises(HTTPException) as exc_info:
        await generate_plan(PlanGenerationRequest.model_validate(request_body()), model=fake)

    assert exc_info.value.status_code == 422


async def test_generate_plan_retries_once_on_invalid_then_succeeds():
    # El reintento único con feedback: la primera respuesta es inválida, la
    # segunda es válida → devuelve el plan.
    fake = FakeToolAwareModel(
        responses=[
            AIMessage(content="no es json"),
            AIMessage(content=json.dumps({"type": "nutrition", "plan": NUTRITION_PLAN})),
        ]
    )
    result = await generate_plan(PlanGenerationRequest.model_validate(request_body()), model=fake)
    assert result.plan["name"] == "Plan personalizado María"


async def test_generate_plan_rejects_sugary_block_violation():
    plan = dict(NUTRITION_PLAN)
    plan["days"] = [dict(plan["days"][0], foods="Galletas con chocolate y azúcar")]
    fake = FakeToolAwareModel(
        responses=[AIMessage(content=json.dumps({"type": "nutrition", "plan": plan}))]
    )

    with pytest.raises(HTTPException) as exc_info:
        await generate_plan(PlanGenerationRequest.model_validate(request_body()), model=fake)

    assert exc_info.value.status_code == 422
    assert "restricciones block" in exc_info.value.detail


async def test_generate_plan_accepts_negated_sugar_mention():
    # "Sin azúcar" / "evitar dulces" NO son violaciones.
    plan = dict(NUTRITION_PLAN)
    plan["days"] = [
        dict(plan["days"][0], notes="Evitar dulces entre comidas", foods="Fruta y avena")
    ]
    fake = FakeToolAwareModel(
        responses=[AIMessage(content=json.dumps({"type": "nutrition", "plan": plan}))]
    )

    result = await generate_plan(PlanGenerationRequest.model_validate(request_body()), model=fake)
    assert result.plan["name"] == "Plan personalizado María"


# ── Endpoint HTTP ───────────────────────────────────────────────────────────


@pytest.fixture
def client():
    app = create_app()
    fake = FakeToolAwareModel(
        responses=[AIMessage(content=json.dumps({"type": "nutrition", "plan": NUTRITION_PLAN}))]
    )

    async def override_service():
        async def service(req):
            return await generate_plan(req, model=fake)

        return service

    app.dependency_overrides[get_plan_generation_service] = override_service
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_plan_endpoint_requires_internal_key(client):
    res = client.post("/internal/wellness/generate-plan", json=request_body())
    assert res.status_code == 401


def test_plan_endpoint_returns_validated_plan(client):
    res = client.post(
        "/internal/wellness/generate-plan",
        json=request_body(),
        headers=HEADERS,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["type"] == "nutrition"
    assert body["plan"]["name"] == "Plan personalizado María"
    assert body["plan"]["days"][0]["day_number"] == 1
