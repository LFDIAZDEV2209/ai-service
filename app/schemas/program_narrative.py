"""Contrato interno para justificar una propuesta clínica ya determinada."""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

SourceRef = Annotated[str, Field(min_length=1, max_length=500, pattern=r"^\S.*")]
NarrativeText = Annotated[str, Field(min_length=1, max_length=2000)]


class ProgramNarrativeRequest(BaseModel):
    """Objetos JSON del backend; los datos clínicos ausentes conservan su null."""

    model_config = ConfigDict(extra="forbid")

    profile_snapshot: dict[str, JsonValue]
    proposal: dict[str, JsonValue]
    language: Literal["es", "en"] = "es"
    source_refs: list[SourceRef] = Field(max_length=100)

    @field_validator("profile_snapshot", "proposal")
    @classmethod
    def limit_json_length(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Acota el contexto enviado al modelo sin reinterpretar el contenido."""
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False)) > 100_000:
            raise ValueError("El objeto JSON no puede superar 100000 caracteres")
        return value


class ProgramNarratives(BaseModel):
    """Ambas traducciones son obligatorias y no admiten null ni texto vacío."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    es: NarrativeText
    en: NarrativeText


class ProgramNarrativeResponse(BaseModel):
    """La respuesta solo contiene texto; no devuelve decisiones clínicas."""

    model_config = ConfigDict(extra="forbid")

    narratives: ProgramNarratives
