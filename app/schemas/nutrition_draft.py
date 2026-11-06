"""Contrato acotado: el backend calcula nutrientes y entrega menús deterministas."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StrictStr, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BilingualText(StrictModel):
    es: str = Field(min_length=1, max_length=4000)
    en: str = Field(min_length=1, max_length=4000)


class NutritionDraftItem(StrictModel):
    food_id: str = Field(min_length=1, max_length=40)
    grams: FiniteFloat = Field(gt=0, le=2000)


class NutritionDraftMenu(StrictModel):
    key: str = Field(pattern=r"^(des|alm|mer|cen):[0-3]$")
    name: str = Field(min_length=1, max_length=200)
    items: list[NutritionDraftItem] = Field(min_length=1, max_length=20)


class NutritionDraftTargets(StrictModel):
    k: FiniteFloat = Field(gt=0)
    c: FiniteFloat = Field(gt=0)
    p: FiniteFloat = Field(gt=0)
    f: FiniteFloat = Field(gt=0)
    fi: FiniteFloat = Field(gt=0)
    water_ml: int = Field(gt=0, le=10000)


class NutritionMeasuredField(StrictModel):
    """Solo valores necesarios para redactar; no identidad del paciente ni respuestas abiertas."""

    value: FiniteFloat | StrictStr | None
    unit: str | None = Field(default=None, max_length=30)
    source: Literal[
        "health_test_response", "health_test_result", "clinical_measurements",
        "clinical_context", "patient_profile", "derived_from_birth_date",
        "derived_from_weight_height", "manual_or_preserved", "missing",
    ]
    source_code: str | None = Field(default=None, max_length=200)
    observed_at: datetime | None = None


class NutritionAppliedRule(StrictModel):
    code: str = Field(min_length=1, max_length=80)
    severity: Literal["crit", "warn", "info"]
    message: str = Field(min_length=1, max_length=4000)
    technical: bool


class NutritionClinicalProfile(StrictModel):
    engine_version: str = Field(min_length=1, max_length=80)
    fields: dict[str, NutritionMeasuredField] = Field(max_length=40)
    applied_rules: list[NutritionAppliedRule] = Field(max_length=100)
    missing_data: list[str] = Field(max_length=40)


class NutritionDraftRequest(StrictModel):
    targets: NutritionDraftTargets
    catalog_ids: list[str] = Field(min_length=1, max_length=200)
    menus: list[NutritionDraftMenu] = Field(min_length=4, max_length=16)
    # Campo opcional: los clientes anteriores conservan el contrato y el fallback.
    clinical_profile: NutritionClinicalProfile | None = None

    @model_validator(mode="after")
    def validate_catalog_and_keys(self) -> Self:
        keys = [m.key for m in self.menus]
        if len(keys) != len(set(keys)):
            raise ValueError("Las claves de menú deben ser únicas")
        if {key.split(":")[0] for key in keys} != {"des", "alm", "mer", "cen"}:
            raise ValueError("Se requieren las cuatro comidas")
        if any(i.food_id not in self.catalog_ids for m in self.menus for i in m.items):
            raise ValueError("Alimento fuera del catálogo")
        return self


class NutritionDraftContent(StrictModel):
    narratives: BilingualText
    instructions: BilingualText
    menus: list[NutritionDraftMenu] = Field(min_length=4, max_length=16)


class NutritionDraftResponse(NutritionDraftContent):
    source: Literal["llm", "deterministic_fallback"]
