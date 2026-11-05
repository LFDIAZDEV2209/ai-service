"""Redacción bilingüe aislada del chat y de los agentes existentes."""

import re
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from app.llm.factory import get_chat_model
from app.schemas.program_narrative import ProgramNarrativeRequest, ProgramNarratives

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def _truncate_narrative(text: str) -> str:
    """Acota a 1999 caracteres con elipsis sin dejar una cita incompleta."""
    if len(text) <= 2000:
        return text
    shortened = text[:1998]
    if shortened.rfind("[") > shortened.rfind("]"):
        shortened = shortened[: shortened.rfind("[")]
    return shortened.rstrip() + "…"


def get_program_narrative_model() -> BaseChatModel:
    """Reutiliza la configuración del servicio; permite sustitución en tests."""
    return get_chat_model(temperature=0.0, max_tokens=2048)


async def generate_program_narrative(
    payload: ProgramNarrativeRequest, model: BaseChatModel
) -> ProgramNarratives:
    """Solicita únicamente texto y comprueba las referencias antes de devolverlo."""
    system = "\n\n".join(
        (PROMPTS_DIR / f"narrative_program_{language}.md").read_text(encoding="utf-8")
        for language in ("es", "en")
    )
    messages = [SystemMessage(content=system), HumanMessage(content=payload.model_dump_json())]
    # Recibir el texto completo permite recuperarlo si excede el contrato público.
    schema = ProgramNarratives.model_json_schema()
    for field in schema["properties"].values():
        field.pop("maxLength", None)
    runnable = model.with_structured_output(schema)
    expected = set(payload.source_refs)
    for attempt in range(3):
        try:
            result = await runnable.ainvoke(messages)
            # Comprobar antes del truncado: nunca ocultar una fuente inventada.
            if isinstance(result, dict) and any(
                not set(re.findall(r"\[([^\[\]]+)\]", text)).issubset(expected)
                for text in (result.get("es"), result.get("en"))
                if isinstance(text, str)
            ):
                continue
            if attempt == 2 and isinstance(result, dict):
                result = {
                    key: _truncate_narrative(value)
                    if key in ("es", "en") and isinstance(value, str)
                    else value
                    for key, value in result.items()
                }
            narratives = ProgramNarratives.model_validate(result)
        except ValidationError:
            if attempt == 2:
                raise
            continue
        if all(
            set(re.findall(r"\[([^\[\]]+)\]", text)).issubset(expected)
            for text in (narratives.es, narratives.en)
        ):
            return narratives
    raise ValueError("La narrativa debe citar únicamente todas las fuentes recibidas")
