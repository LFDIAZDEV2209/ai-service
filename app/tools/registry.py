"""Registro central de herramientas (patrón del curso: definición separada de ejecución).

Cada tool se define en `app/tools/builtin.py` con el decorador `@tool` de LangChain,
que deriva su esquema JSON automáticamente de la firma + docstring.
Aquí se recolectan una sola vez y se exponen al LLM vía `bind_tools`.
"""

from langchain_core.tools import BaseTool

from app.tools import builtin


def _collect_tools() -> list[BaseTool]:
    """Recolecta todas las tools decoradas con @tool del módulo builtin."""
    tools: list[BaseTool] = []
    for name in dir(builtin):
        obj = getattr(builtin, name)
        if isinstance(obj, BaseTool):
            tools.append(obj)
    return sorted(tools, key=lambda t: t.name)


ALL_TOOLS: list[BaseTool] = _collect_tools()
TOOLS_BY_NAME: dict[str, BaseTool] = {t.name: t for t in ALL_TOOLS}


def list_tool_names() -> list[str]:
    return list(TOOLS_BY_NAME)
