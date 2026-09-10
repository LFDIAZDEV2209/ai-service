"""LLM falsos para tests — permiten probar el flujo completo sin API keys."""

from __future__ import annotations

import json
from collections.abc import Sequence

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableLambda
from pydantic import Field


class FakeToolAwareModel(FakeMessagesListChatModel):
    """FakeMessagesListChatModel que tolera `bind_tools` y `with_structured_output`.

    - `bind_tools` simplemente devuelve `self` (el fake ignora las tools).
    - `with_structured_output` devuelve un Runnable que toma el AIMessage de la
      lista de respuestas, extrae el JSON (tolera el wrapper `{"plan": ...}`) y
      lo valida contra el esquema Pydantic — reproduciendo el comportamiento del
      proveedor real sin API keys.
    """

    def bind_tools(
        self,
        tools: list,
        *,
        tool_choice: str | None = None,
        **kwargs,
    ) -> FakeToolAwareModel:
        return self

    def with_structured_output(self, schema, **kwargs):
        responses: list[BaseMessage] = list(self.responses)

        def _parse(_messages: list[BaseMessage]):
            if not responses:
                raise AssertionError("FakeToolAwareModel se quedó sin respuestas")
            message = responses.pop(0)
            content = message.content
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            data = json.loads(content)
            plan_data = data.get("plan") if isinstance(data, dict) and "plan" in data else data
            return schema.model_validate(plan_data)

        return RunnableLambda(_parse)


class RecordingFakeModel(FakeToolAwareModel):
    """FakeToolAwareModel que además captura cada prompt que recibe.

    Permite verificar verbatim el prompt de sistema (p. ej. el bloque de guía
    del control de programa) sin API keys. `_generate` es el embudo único por
    el que pasan tanto `ainvoke` como `bind_tools(...).ainvoke` (el default de
    `_agenerate` lo ejecuta en un executor).
    """

    prompts: list[list[BaseMessage]] = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.prompts.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


class FakeStructuredModel:
    """Modelo fake mínimo para `with_structured_output` (sin LangChain fake).

    Recibe respuestas como dicts ya validables o JSON strings; `ainvoke` no se
    usa: solo `with_structured_output(...).ainvoke(...)` devuelve el objeto
    Pydantic. Útil cuando el fake real no aplica.
    """

    def __init__(self, responses: Sequence[AIMessage | dict]):
        self._responses: list = list(responses)

    def with_structured_output(self, schema, **kwargs):
        responses = self._responses

        def _parse(_messages: list[BaseMessage]):
            if not responses:
                raise AssertionError("FakeStructuredModel se quedó sin respuestas")
            raw = responses.pop(0)
            if isinstance(raw, dict):
                return schema.model_validate(raw)
            content = raw.content
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            data = json.loads(content)
            plan_data = data.get("plan") if isinstance(data, dict) and "plan" in data else data
            return schema.model_validate(plan_data)

        return RunnableLambda(_parse)
