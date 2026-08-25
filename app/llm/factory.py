"""Fábrica de modelos LLM multi-proveedor (Anthropic / OpenAI).

Es la abstracción que permite cambiar de proveedor por agente o por entorno
sin tocar la lógica del grafo: todo el resto del código habla con
`BaseChatModel` de LangChain.
"""


from langchain_core.language_models.chat_models import BaseChatModel

from app.core.config import get_settings
from app.core.errors import ConfigError


def get_chat_model(
    provider: str | None = None,
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> BaseChatModel:
    """Crea un modelo de chat según el proveedor indicado (o el configurado).

    Args:
        provider: "anthropic" | "openai". Por defecto usa `settings.llm_provider`.
        model: nombre del modelo (por defecto el configurado por proveedor).
        temperature: temperatura (por defecto la configurada).
        max_tokens: tope de tokens de salida (solo Anthropic).

    Raises:
        ConfigError: proveedor desconocido o falta la API key.
    """
    settings = get_settings()
    provider = (provider or settings.llm_provider).strip().lower()
    temperature = settings.llm_temperature if temperature is None else temperature
    max_tokens = settings.llm_max_tokens if max_tokens is None else max_tokens

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ConfigError("ANTHROPIC_API_KEY no está configurada")
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=model or settings.anthropic_model,
            api_key=settings.anthropic_api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            streaming=True,
        )

    if provider == "openai":
        if not settings.openai_api_key:
            raise ConfigError("OPENAI_API_KEY no está configurada")
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model or settings.openai_model,
            api_key=settings.openai_api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            streaming=True,
        )

    raise ConfigError(
        f"Proveedor LLM desconocido: {provider!r}. Usa 'anthropic' u 'openai'."
    )
