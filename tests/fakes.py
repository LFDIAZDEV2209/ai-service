"""LLM falsos para tests — permiten probar el flujo completo sin API keys."""

from __future__ import annotations

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel


class FakeToolAwareModel(FakeMessagesListChatModel):
    """FakeMessagesListChatModel que tolera `bind_tools`.

    El fake real de langchain-core lanza `NotImplementedError` en `bind_tools`.
    Como el fake ignora las tools, simplemente devolvemos `self`.
    """

    def bind_tools(
        self,
        tools: list,
        *,
        tool_choice: str | None = None,
        **kwargs,
    ) -> FakeToolAwareModel:
        return self
