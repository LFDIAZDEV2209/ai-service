"""Contrato interno para explicar evaluaciones sin convertir puntuaciones en diagnósticos."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400)]


class AssessmentSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    code: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=300)
    value: float
    qualifier: str | None = Field(default=None, max_length=500)
    severity: str | None = Field(default=None, max_length=40)


class AssessmentAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: Literal["es", "en"] = "es"
    results: list[AssessmentSignal] = Field(min_length=1, max_length=250)


class AssessmentPriority(BaseModel):
    title: str = Field(min_length=5, max_length=120)
    explanation: str = Field(min_length=20, max_length=1000)
    action: str = Field(min_length=10, max_length=500)
    evidence_codes: list[str] = Field(min_length=1, max_length=10)


class AssessmentAnalysisResponse(BaseModel):
    summary: str = Field(min_length=40, max_length=1600)
    priorities: list[AssessmentPriority] = Field(min_length=1, max_length=3)
    encouragement: str = Field(min_length=10, max_length=400)
    questions: list[ShortText] = Field(default_factory=list, max_length=3)
