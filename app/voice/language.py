"""Puente mínimo de la tool de idioma del proveedor; nunca ejecuta tools de datos."""

import json
from typing import Any

from app.graph.language import detect_turn_language


def latest_user_index(messages) -> int:
    return max((i for i, message in enumerate(messages) if message.role == "user"), default=-1)


def conversation_language(messages, initial: str) -> str:
    """Las intervenciones claras conservan idioma para respuestas breves/neutras."""
    current = initial
    for message in messages:
        if message.role == "user":
            current = detect_turn_language({"input": message.content or ""}) or current
    return current


def language_switch(messages, tools: list[dict[str, Any]], initial: str) -> str | None:
    """Decide ES/EN con el texto, sin confiar en prompts ni argumentos del proveedor.

    ElevenLabs devuelve el resultado de language_detection antes de solicitar
    la respuesta. Ese ciclo no es otro turno del paciente ni consume un recibo.
    """
    if not any(
        tool.get("type") == "function"
        and isinstance(tool.get("function"), dict)
        and tool["function"].get("name") == "language_detection"
        for tool in tools
    ):
        return None
    current = initial
    for message in messages:
        for call in message.tool_calls:
            function = call.get("function", {})
            if not isinstance(function, dict) or function.get("name") != "language_detection":
                continue
            try:
                args = json.loads(function.get("arguments", "{}"))
            except (TypeError, ValueError):
                continue
            if isinstance(args, dict) and args.get("language") in {"en", "es"}:
                current = args["language"]
    index = latest_user_index(messages)
    if index < 0:
        return None
    language = detect_turn_language({"input": messages[index].content or ""})
    return language if language and language != current else None


def valid_language_followup(messages) -> bool:
    """Acepta únicamente el retorno acotado de nuestra tool de idioma.

    Los mensajes de proveedor siguen siendo datos: no se importan al prompt,
    no seleccionan identidad/modelo/KB ni autorizan herramientas de la app.
    """
    index = latest_user_index(messages)
    tail = messages[index + 1 :]
    if index < 0 or len(tail) != 2:
        return False
    assistant, result = tail
    if assistant.role != "assistant" or result.role != "tool" or not result.tool_call_id:
        return False
    if len(assistant.tool_calls) != 1:
        return False
    call = assistant.tool_calls[0]
    return bool(
        call.get("id") == result.tool_call_id
        and isinstance(call.get("function"), dict)
        and call["function"].get("name") == "language_detection"
    )


def language_chunk(response_id: str, language: str) -> dict:
    """Function call OpenAI para el control nativo del idioma en ElevenLabs."""
    return {
        "index": 0,
        "id": f"{response_id}:language",
        "type": "function",
        "function": {
            "name": "language_detection",
            "arguments": json.dumps(
                {"reason": "Match the user's spoken language", "language": language}
            ),
        },
    }
