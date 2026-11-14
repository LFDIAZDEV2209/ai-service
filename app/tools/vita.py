"""Seis capacidades selectivas sin argumentos elegidos por el modelo."""

from langchain_core.tools import StructuredTool
from langgraph.config import get_config

from app.vita.client import read_context
from app.vita.contracts import ContextEnvelope, EmptyArguments, Scope

VITA_READ_TOOL_NAMES = frozenset(
    {
        "get_my_day",
        "get_my_nutrition",
        "get_my_assessments",
        "get_my_podcast",
        "get_my_exercise",
        "get_my_progress",
    }
)


def make_context_tool(name: str, scope: Scope, description: str) -> StructuredTool:
    async def read() -> str:
        user_id = get_config().get("configurable", {}).get("user_id")
        result = (
            await read_context(scope, str(user_id))
            if user_id
            else ContextEnvelope(status="forbidden")
        )
        return result.model_dump_json(exclude_none=True)

    return StructuredTool.from_function(
        coroutine=read,
        name=name,
        description=description,
        args_schema=EmptyArguments,
    )


get_my_day = make_context_tool(
    "get_my_day",
    "day",
    "Lee el programa y tareas efectivos de hoy del paciente autenticado.",
)
get_my_nutrition = make_context_tool(
    "get_my_nutrition",
    "nutrition",
    "Lee solo el menú publicado vigente de hoy; nunca borradores.",
)
get_my_assessments = make_context_tool(
    "get_my_assessments",
    "assessments",
    "Lee tests asignados propios; no responde ni envía tests.",
)
get_my_podcast = make_context_tool(
    "get_my_podcast",
    "podcast",
    "Lee el podcast asignado hoy y su atajo autorizado; abrir no da XP.",
)
get_my_exercise = make_context_tool(
    "get_my_exercise",
    "exercise",
    "Lee rutina efectiva o descanso explícito de hoy sin cambiarla.",
)
get_my_progress = make_context_tool(
    "get_my_progress",
    "progress",
    "Lee progreso registrado con periodo y fuentes, sin juicio clínico.",
)
