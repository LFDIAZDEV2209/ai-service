"""Registro central de herramientas (patrón del curso: definición separada de ejecución).

Cada tool se define en un módulo de `app/tools/` (builtin, appointment...) con
el decorador `@tool` de LangChain, que deriva su esquema JSON automáticamente
de la firma + docstring. Aquí se recolectan una sola vez y se exponen al LLM
vía `bind_tools`. Para registrar un módulo nuevo, agregalo a `_TOOL_MODULES`.
"""

from langchain_core.tools import BaseTool

from app.tools import appointment, builtin, control, patient_context, vita

# Módulos escaneados en busca de tools decoradas con @tool (orden no importa:
# el resultado final se ordena por nombre).
_TOOL_MODULES = (builtin, appointment, control, patient_context, vita)


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

# Compatibilidad congelada: registrar una tool nueva nunca concede su capacidad
# a una versión antigua del ERP. Las citas están retiradas del canal paciente.
LEGACY_TOOL_NAMES = frozenset(
    {
        "calculate",
        "get_current_time",
        "get_patient_data",
        "mark_control_declined",
        "suggest_appointment",
    }
)
DISABLED_PATIENT_TOOLS = frozenset({"suggest_appointment"})


def patient_tools(tools: list[BaseTool]) -> list[BaseTool]:
    """Aplica la política también a listas explícitas y tools de perfiles ERP."""
    return [tool for tool in tools if tool.name not in DISABLED_PATIENT_TOOLS]


def legacy_tools(*, include_patient: bool = False) -> list[BaseTool]:
    names = LEGACY_TOOL_NAMES - DISABLED_PATIENT_TOOLS
    if not include_patient:
        names = names - {"get_patient_data"}
    return [TOOLS_BY_NAME[name] for name in sorted(names)]


def list_tool_names() -> list[str]:
    return list(TOOLS_BY_NAME)
