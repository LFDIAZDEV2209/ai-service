"""Esquemas Pydantic de la generación de planes de bienestar (wellness) con IA.

Canal interno backend .NET → AI Service: el backend envía el contexto clínico
del paciente + restricciones de seguridad, y el AI Service devuelve un plan
estructurado (alimentación o rutina) validado, listo para usarse como payload
de creación.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PatientIn(BaseModel):
    """Datos básicos del paciente."""

    age: int = Field(ge=0, le=130, description="Edad en años")
    gender: str = Field(min_length=1, description="Género reportado (M/F/otro)")
    exercise_level: str = Field(default="", description="Nivel de ejercicio reportado")


class MeasurementIn(BaseModel):
    """Medición clínica (glucemia, peso, presión, etc.)."""

    metric: str = Field(min_length=1, description="Identificador de la métrica")
    value: float = Field(description="Valor medido")
    unit: str = Field(min_length=1, description="Unidad de la métrica (ej: mg_dl)")
    observed_at: datetime = Field(description="Momento de la medición (ISO 8601)")


class LifestyleIn(BaseModel):
    """Estilo de vida reportado por el paciente."""

    smoking: str = Field(default="", description="Hábito tabáquico")
    alcohol: str = Field(default="", description="Consumo de alcohol")
    exercise_level: str = Field(default="", description="Nivel de ejercicio")


class ClinicalContextIn(BaseModel):
    """Contexto clínico completo que el LLM usa como base del plan."""

    patient: PatientIn
    measurements: list[MeasurementIn] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    diagnoses: list[str] = Field(default_factory=list)
    medications: list[str] = Field(default_factory=list)
    lifestyle: LifestyleIn = Field(default_factory=LifestyleIn)


class RestrictionIn(BaseModel):
    """Restricción de seguridad del plan.

    `block`: obligatoria (el plan NO puede violarla).
    `warning`: informativa (debe tenerse en cuenta sin ser bloqueante).
    """

    rule: str = Field(min_length=1, description="Regla de restricción en lenguaje natural")
    severity: Literal["block", "warning"]


class PlanGenerationRequest(BaseModel):
    """Payload del endpoint interno `POST /internal/wellness/generate-plan`."""

    type: Literal["nutrition", "exercise"]
    clinical_context: ClinicalContextIn
    restrictions: list[RestrictionIn] = Field(default_factory=list)


class NutritionDayOut(BaseModel):
    """Comida de un día del plan de alimentación."""

    model_config = ConfigDict(extra="ignore")

    day_number: int = Field(ge=1)
    meal_type: str = Field(description="Ej: Desayuno, Almuerzo, Merienda, Cena")
    description: str = Field(default="", description="Descripción de la comida")
    foods: str = Field(default="", description="Alimentos de la comida (texto libre del LLM)")
    calories: int = Field(ge=0)
    protein_g: float = Field(default=0, ge=0)
    carbs_g: float = Field(default=0, ge=0)
    fat_g: float = Field(default=0, ge=0)
    fiber_g: float = Field(default=0, ge=0)
    water_ml: int = Field(default=0, ge=0)
    notes: str = Field(default="", description="Notas / recomendaciones de la comida")


class NutritionPlanOut(BaseModel):
    """Plan de alimentación estructurado listo para el backend .NET."""

    model_config = ConfigDict(extra="ignore")

    name: str
    description: str = Field(default="")
    target_condition: str = Field(default="", description="Condición objetivo (ej: Prediabetes)")
    duration_days: int = Field(ge=1)
    daily_calorie_target: int = Field(ge=0)
    daily_protein_target: float = Field(default=0, ge=0)
    daily_carbs_target: float = Field(default=0, ge=0)
    daily_fat_target: float = Field(default=0, ge=0)
    daily_fiber_target: float = Field(default=0, ge=0)
    allergens: str = Field(default="Ninguno", description="Alérgenos considerados")
    meal_timing: str = Field(
        default="", description="Horarios separados por coma (ej: 7:00, 12:00)"
    )
    days: list[NutritionDayOut] = Field(min_length=1)


class RoutineExerciseOut(BaseModel):
    """Ejercicio individual dentro de una rutina."""

    model_config = ConfigDict(extra="ignore")

    name: str
    description: str = Field(default="", description="Cómo ejecutar el ejercicio")
    sets: int = Field(ge=0)
    repetitions: int | None = Field(default=None, ge=0)
    rest_seconds: int = Field(default=0, ge=0)
    duration_secs: int | None = Field(
        default=None, ge=0, description="Duración para ejercicios por tiempo"
    )
    weight_kg: float | None = Field(default=None, ge=0)
    target_muscle: str = Field(default="")
    equipment: str = Field(default="")
    tempo: str = Field(default="", description="Cadencia (ej: 2-1-2)")
    rpe: int | None = Field(default=None, ge=1, le=10, description="Esfuerzo percibido 1-10")
    tips: str = Field(default="", description="Consejos de ejecución/seguridad")


class ExerciseRoutineOut(BaseModel):
    """Rutina de ejercicio estructurada lista para el backend .NET."""

    model_config = ConfigDict(extra="ignore")

    name: str
    description: str = Field(default="")
    difficulty: str = Field(default="Moderado", description="Ej: Bajo, Moderado, Alto")
    estimated_minutes: int = Field(ge=0)
    category: str = Field(default="Mixta", description="Ej: Fuerza, Cardio, Mixta")
    target_muscles: str = Field(default="", description="Músculos objetivo (ej: Piernas, Core)")
    equipment: str = Field(default="", description="Equipamiento requerido")
    warmup_notes: str = Field(default="", description="Indicaciones de calentamiento")
    cooldown_notes: str = Field(default="", description="Indicaciones de enfriamiento")
    exercises: list[RoutineExerciseOut] = Field(min_length=1)


class PlanGenerationResponse(BaseModel):
    """Respuesta del endpoint: tipo + plan validado como dict para el backend.

    `plan` es `dict[str, Any]` porque su esquema interno depende de `type`
    (NutritionPlanOut o ExerciseRoutineOut ya validados por el servicio).
    """

    type: str
    plan: dict[str, Any]
