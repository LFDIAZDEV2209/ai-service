"""Jerarquía de excepciones del dominio del servicio de IA."""


class CoppAiError(Exception):
    """Error base de todo el servicio."""


class ConfigError(CoppAiError):
    """Configuración inválida o incompleta (ej: falta una API key)."""


class GuardrailError(CoppAiError):
    """El input/output fue bloqueado por los guardrails de seguridad."""


class ProviderError(CoppAiError):
    """Error al comunicarse con el proveedor LLM."""


class ToolExecutionError(CoppAiError):
    """Error al ejecutar una herramienta."""


class MemoryStoreError(CoppAiError):
    """Error en la capa de memoria/persistencia."""
