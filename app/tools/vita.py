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
    "Read my effective program and tasks for today / programa y tareas de hoy.",
)
get_my_nutrition = make_context_tool(
    "get_my_nutrition",
    "nutrition",
    "Read today's current published nutrition plan and meals / plan nutricional y menú; no drafts.",
)
get_my_assessments = make_context_tool(
    "get_my_assessments",
    "assessments",
    "Read my assigned tests / mis evaluaciones; never answer or submit them.",
)
get_my_podcast = make_context_tool(
    "get_my_podcast",
    "podcast",
    "Read my assigned podcast and authorized shortcut / podcast asignado; no XP on open.",
)
get_my_exercise = make_context_tool(
    "get_my_exercise",
    "exercise",
    "Read my effective exercise or rest day / rutina o descanso de hoy; no changes.",
)
get_my_progress = make_context_tool(
    "get_my_progress",
    "progress",
    "Read my recorded progress with period/sources / mi progreso; no clinical judgments.",
)
