"""Registro central de herramientas (patrón del curso: definición separada de ejecución).

Cada tool se define en un módulo de `app/tools/` (builtin, appointment...) con
el decorador `@tool` de LangChain, que deriva su esquema JSON automáticamente
de la firma + docstring. Aquí se recolectan una sola vez y se exponen al LLM
vía `bind_tools`. Para registrar un módulo nuevo, agregalo a `_TOOL_MODULES`.
"""

from langchain_core.tools import BaseTool

from app.tools import appointment, builtin

# Módulos escaneados en busca de tools decoradas con @tool (orden no importa:
# el resultado final se ordena por nombre).
_TOOL_MODULES = (builtin, appointment)


def _collect_tools() -> list[BaseTool]:
    """Recolecta todas las tools decoradas con @tool de los módulos registrados."""
    tools: list[BaseTool] = []
    for module in _TOOL_MODULES:
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, BaseTool):
                tools.append(obj)
    return sorted(tools, key=lambda t: t.name)


ALL_TOOLS: list[BaseTool] = _collect_tools()
TOOLS_BY_NAME: dict[str, BaseTool] = {t.name: t for t in ALL_TOOLS}


def list_tool_names() -> list[str]:
    return list(TOOLS_BY_NAME)
