"""Servicio de generación de planes de bienestar (alimentación / rutina) con IA.

Canal interno: el backend .NET envía el contexto clínico del paciente +
restricciones de seguridad; este servicio llama al LLM con temperatura baja,
extrae el JSON de la respuesta (tolera fences de markdown), lo valida contra
el esquema de salida según el tipo y devuelve el plan listo como payload.

Si el JSON no valida, se reintenta UNA vez retroalimentando el error al LLM;
si sigue fallando → HTTPException 422. Las restricciones `block` se chequean
de forma razonable (búsqueda de términos, no análisis semántico perfecto).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from app.core.config import get_settings
from app.core.errors import ConfigError
from app.llm.factory import get_chat_model
from app.schemas.plan_generation import (
    ExerciseRoutineOut,
    NutritionPlanOut,
    PlanGenerationRequest,
    PlanGenerationResponse,
)

logger = logging.getLogger(__name__)

# Términos que delatan una restricción block sobre azúcar / carbohidratos de alto IG.
_SUGAR_RULE_TERMS = (
    "azúcar",
    "azucar",
    "azúcares",
    "azucares",
    "azucarado",
    "azucarados",
    "azucarada",
    "azucaradas",
    "dulce",
    "dulces",
    "alto índice glucémico",
    "alto indice glucemico",
    "índice glucémico",
    "indice glucemico",
)

# Alimentos/ingredientes azucarados que delatan una violación dentro del plan.
_SUGARY_FOOD_TERMS = (
    "azúcar",
    "azucar",
    "azúcar añadida",
    "azucar anadida",
    "azucarado",
    "azucarados",
    "azucarada",
    "azucaradas",
    "dulce",
    "dulces",
    "chocolate",
    "caramelo",
    "caramelos",
    "pastel",
    "pasteles",
    "galleta",
    "galletas",
    "refresco",
    "refrescos",
    "gaseosa",
    "gaseosas",
    "bebida azucarada",
    "bebidas azucaradas",
    "miel",
    "mermelada",
    "helado",
    "helados",
    "postre",
    "postres",
    "jugo",
    "jugos",
    "zumo",
    "zumos",
    "jarabe",
    "sirope",
    "endulzado",
    "endulzada",
    "azúcar moreno",
    "pan dulce",
    "cereal azucarado",
    "cereales azucarados",
    "flan",
    "torta",
    "barrita de chocolate",
)

# Firma inyectable del servicio (tests sin API keys la sobreescriben).
PlanGenerationService = Callable[[PlanGenerationRequest], Awaitable[PlanGenerationResponse]]


def _build_prompt(request: PlanGenerationRequest) -> str:
    """Construye el prompt con el perfil, mediciones, alergias/dx/meds,
    estilo de vida y las RESTRICCIONES marcadas como OBLIGATORIAS."""
    ctx = request.clinical_context
    patient = ctx.patient

    measurements = "\n".join(
        f"- {m.metric}: {m.value} {m.unit} (observado {m.observed_at.isoformat()})"
        for m in ctx.measurements
    ) or "- Sin mediciones."

    restrictions = "\n".join(
        f"- [{r.severity.upper()}] {r.rule}" for r in request.restrictions
    ) or "- Sin restricciones."

    if request.type == "nutrition":
        plan_kind = "plan de alimentación (nutrición)"
        schema_hint = (
            "name, description, target_condition, duration_days, daily_calorie_target, "
            "daily_protein_target, daily_carbs_target, daily_fat_target, daily_fiber_target, "
            "allergens, meal_timing, days[] (day_number, meal_type, description, foods, "
            "calories, protein_g, carbs_g, fat_g, fiber_g, water_ml, notes)"
        )
    else:
        plan_kind = "rutina de ejercicio"
        schema_hint = (
            "name, description, difficulty, estimated_minutes, category, target_muscles, "
            "equipment, warmup_notes, cooldown_notes, exercises[] (name, description, sets, "
            "repetitions, rest_seconds, duration_secs, weight_kg, target_muscle, equipment, "
            "tempo, rpe, tips)"
        )

    patient_line = (
        f"- Edad: {patient.age} años | Género: {patient.gender} | "
        f"Nivel de ejercicio: {patient.exercise_level or 'No reportado'}"
    )
    lifestyle_line = (
        f"- Estilo de vida: tabaquismo: {ctx.lifestyle.smoking or 'No reportado'} | "
        f"alcohol: {ctx.lifestyle.alcohol or 'No reportado'} | "
        f"ejercicio: {ctx.lifestyle.exercise_level or 'No reportado'}"
    )

    return f"""Eres un especialista en salud que diseña {plan_kind} personalizados.

Genera SOLO un objeto JSON válido (sin texto adicional, sin fences de markdown) con esta forma:
{{
  "type": "{request.type}",
  "plan": {{
    {schema_hint}
  }}
}}

CONTEXTO CLÍNICO DEL PACIENTE
{patient_line}
- Mediciones: {measurements}
- Alergias: {", ".join(ctx.allergies) or "Ninguna"}
- Diagnósticos: {", ".join(ctx.diagnoses) or "Sin diagnósticos"}
- Medicación: {", ".join(ctx.medications) or "Sin medicación"}
{lifestyle_line}

RESTRICCIONES DE SEGURIDAD (OBLIGATORIAS — NO LAS VIOLES):
{restrictions}

Las restricciones con severidad BLOCK son innegociables: el plan NO puede incluir
alimentos ni ejercicios que las violen. Respondé únicamente con el JSON."""


def _extract_json(text: str) -> dict[str, Any]:
    """Extrae el primer objeto JSON del texto (tolera fences de markdown).

    El LLM a veces devuelve texto de más o commas finales: se limpia con un
    parser tolerante (elimina trailing commas) y luego se corta al bloque
    llave a llave más grande antes de delegar en `json.loads`.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        # Descarta la primera línea (``` o ```json) y, si quedó, la última (```).
        cleaned = "\n".join(lines[1:])
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]

    # json.loads no tolera trailing commas: removerlas antes de parsear.
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        stripped = re.sub(r",\s*([}\]])", r"\1", cleaned)
        return json.loads(stripped)


# Palabras de negación: una mención azucarada precedida por ellas NO es una violación
# (ej: "sin azúcar añadida", "evitar dulces", "bajo en azúcar").
_NEGATION_WORDS = (
    "sin ",
    "evitar ",
    "evite ",
    "evita ",
    "evitando ",
    "bajo en ",
    "baja en ",
    "libre de ",
    "no consumir ",
)


def _mentions_sugar(rule: str) -> bool:
    """True si la regla menciona azúcar / carbohidratos de alto índice glucémico."""
    lowered = rule.lower()
    return any(term in lowered for term in _SUGAR_RULE_TERMS)


def _plan_contains_sugary_food(plan: dict[str, Any]) -> bool:
    """True si el plan incluye alimentos azucarados (ignora menciones negadas).

    Heurística razonable: recorre cada término azucarado y descarta las
    ocurrencias precedidas por una negación ("sin azúcar" no es una violación).
    """
    plan_text = json.dumps(plan, ensure_ascii=False).lower()
    for term in _SUGARY_FOOD_TERMS:
        for match in re.finditer(re.escape(term), plan_text):
            before = plan_text[max(0, match.start() - 15) : match.start()]
            if any(neg in before for neg in _NEGATION_WORDS):
                continue  # mención negada: no es una violación
            return True
    return False


def _check_block_restrictions(request: PlanGenerationRequest, plan: dict[str, Any]) -> None:
    """Chequeo razonable de restricciones `block`.

    No es análisis semántico perfecto: si una restricción block menciona
    azúcar/IG alto Y el plan lista alimentos azucarados en `foods` o
    descripciones, el plan se rechaza con 422.
    """
    sugary_blocks = [
        r.rule for r in request.restrictions if r.severity == "block" and _mentions_sugar(r.rule)
    ]
    if not sugary_blocks:
        return

    if _plan_contains_sugary_food(plan):
        raise HTTPException(
            status_code=422,
            detail=(
                "El plan generado viola restricciones block: incluye alimentos azucarados "
                "pese a las reglas: " + "; ".join(sugary_blocks)
            ),
        )


async def generate_plan(
    request: PlanGenerationRequest,
    model: BaseChatModel | None = None,
) -> PlanGenerationResponse:
    """Genera un plan (nutrición o rutina) a partir del contexto clínico.

    Args:
        request: contexto clínico del paciente + restricciones de seguridad.
        model: modelo de chat inyectable (tests usan fakes); por defecto usa
            el factory multi-proveedor con temperatura baja.

    Raises:
        HTTPException 422: el LLM no produce JSON válido tras el reintento o el
            plan viola una restricción block.
        HTTPException 503: proveedor LLM no configurado (falta API key).
        HTTPException 504: el LLM no responde dentro del timeout configurado.
    """
    settings = get_settings()
    try:
        model = model or get_chat_model(
            temperature=settings.plan_generation_temperature,
            max_tokens=settings.plan_generation_max_tokens,
        )
    except ConfigError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Servicio no configurado: {exc}",
        ) from exc

    prompt_text = _build_prompt(request)
    # Anthropic exige al menos un mensaje con rol user: las instrucciones y el
    # contexto van como system, y el pedido concreto como human.
    messages: list[SystemMessage | HumanMessage] = [
        SystemMessage(content=prompt_text),
        HumanMessage(content="Generá el plan según las instrucciones y el contexto."),
    ]

    # Structured output: el proveedor (Anthropic/OpenAI) recibe el esquema
    # Pydantic como "tool" y devuelve el objeto ya tipado — sin parsear JSON.
    schema = NutritionPlanOut if request.type == "nutrition" else ExerciseRoutineOut
    structured = model.with_structured_output(schema)

    last_error: Exception | None = None
    plan_model: BaseModel | None = None
    for attempt in range(2):  # original + 1 reintento con feedback
        try:
            plan_model = await asyncio.wait_for(
                structured.ainvoke(messages),
                timeout=settings.plan_generation_timeout,
            )
            break
        except TimeoutError:
            raise HTTPException(
                status_code=504,
                detail=(
                    f"El LLM tardó más de {settings.plan_generation_timeout}s "
                    "en generar el plan"
                ),
            ) from None
        except ValidationError as exc:
            # Esquema incompleto/incorrecto: reintentar una vez con feedback
            # del error para que el LLM complete o corrija.
            last_error = exc
            logger.warning(
                "Plan no válido (intento %s, type=%s): %s",
                attempt, request.type, exc,
            )
            if attempt == 0:
                feedback = (
                    "El plan que generaste no cumple el esquema requerido. "
                    f"Errores de validación: {exc}. Corregí el plan completo "
                    "respetando TODOS los campos, incluyendo el array days/exercises."
                )
                messages = [
                    SystemMessage(content=prompt_text),
                    HumanMessage(content=feedback),
                ]
                continue
            break
        except Exception as exc:
            # Otro fallo (truncación, provider, parser...): si es el primer
            # intento, reintentar una vez con pedido de completar.
            last_error = exc
            logger.warning(
                "Fallo generando plan (intento %s, type=%s): %s",
                attempt, request.type, exc,
            )
            if attempt == 0:
                feedback = (
                    "Tu respuesta anterior fue truncada o incompleta. "
                    f"Detalle: {exc}. Generá el plan completo otra vez con TODOS "
                    "los días/comidas (o ejercicios), sin omitir campos."
                )
                messages = [
                    SystemMessage(content=prompt_text),
                    HumanMessage(content=feedback),
                ]
                continue
            break

    if plan_model is None:
        detail = _format_failure(last_error)
        logger.error("Generación de plan falló (type=%s): %s", request.type, detail)
        raise HTTPException(status_code=422, detail=detail)

    # El chequeo de restricciones block es clínico: no se reintenta con el LLM,
    # se rechaza el plan directamente.
    plan_dict = plan_model.model_dump()
    _check_block_restrictions(request, plan_dict)
    return PlanGenerationResponse(type=request.type, plan=plan_dict)


def _format_failure(error: Exception | None) -> str:
    """Mensaje legible del fallo de generación tras agotar los reintentos."""
    if isinstance(error, ValidationError):
        errors = "; ".join(
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
            for err in error.errors()
        )
        return f"El LLM no produjo un plan válido tras 2 intentos. Errores: {errors}"
    if error and "truncated" in str(error).lower():
        return (
            "La respuesta del LLM fue truncada por el límite de tokens tras 2 "
            "intentos. Aumentá plan_generation_max_tokens o reducí la duración "
            "del plan."
        )
    return (
        "El LLM no produjo un plan válido tras 2 intentos. "
        f"Detalle: {error or 'desconocido'}"
    )


async def get_plan_generation_service() -> PlanGenerationService:
    """Dependencia FastAPI: devuelve la función de generación de planes.

    Se inyecta en el router interno; los tests la sobreescriben con
    `app.dependency_overrides` para usar un LLM fake sin API keys.
    """
    return generate_plan
