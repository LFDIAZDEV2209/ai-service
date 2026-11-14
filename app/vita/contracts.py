"""Contrato BACK v1: lecturas vigentes y navegación acotada, sin escrituras."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

Scope = Literal["day", "nutrition", "assessments", "podcast", "exercise", "progress"]


class AppAction(BaseModel):
    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, serialize_by_alias=True)

    type: Literal["navigate"]
    destination: Literal["program", "nutrition", "assessments", "podcast", "exercise", "progress"]
    resource_id: str | None = None
    local_date: date
    resource_version: str = Field(min_length=1)


class ContextEnvelope(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, serialize_by_alias=True)
    status: Literal["ok", "empty", "unavailable", "forbidden", "stale"]
    as_of: datetime | None = None
    local_date: date | None = None
    resource_version: str | None = None
    data: Any = None
    actions: list[AppAction] = Field(default_factory=list)
    sources: list[dict[str, Any] | str] = Field(default_factory=list)


class EmptyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
