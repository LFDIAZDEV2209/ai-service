"""Contrato acotado para traducir textos publicados sin modificar su fuente."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ContentTranslationItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=8000)

    @field_validator("id", "text")
    @classmethod
    def require_visible_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("El texto y su identificador no pueden estar vacíos.")
        return value


class ContentTranslationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    target_language: Literal["en"]
    items: list[ContentTranslationItem] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_batch(self) -> Self:
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("Los identificadores deben ser únicos.")
        if sum(len(item.text) for item in self.items) > 32000:
            raise ValueError("El lote excede el límite de texto.")
        return self


class TranslatedContentItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1, max_length=128)
    translation: str = Field(min_length=1, max_length=16000)


class ContentTranslationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    target_language: Literal["en"]
    items: list[TranslatedContentItem] = Field(min_length=1, max_length=64)
