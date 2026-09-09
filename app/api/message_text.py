"""Extracción de texto plano desde `content` de mensajes del LLM.

Claude (Anthropic) devuelve `content` como lista de bloques
(`[{"type": "text", "text": ...}, {"type": "tool_use", ...}]`); serializar
la lista cruda con `str()` muestra `"[{'text': ...}]"` en el chat
(repr de Python, con `\n` literales). Este helper centraliza la
extracción para todas las rutas que leen mensajes: `POST /chat`,
`POST /chat/stream` (vía `_extract_answer`) y `GET /threads/.../state`.
"""

from __future__ import annotations

from typing import Any


def extract_message_text(content: Any) -> str:
    """Devuelve el texto legible de un `content` de mensaje.

    - `str` → tal cual.
    - `list` → concatena los bloques `{"type": "text", "text": ...}`
      (dicts, como los devuelve el checkpointer tras deserializar) y los
      objetos con atributo `.text`; omite bloques no textuales
      (`tool_use`, imágenes) en lugar de serializarlos.
    - `None` → `""`; cualquier otro tipo → `str()` (comportamiento previo).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
            else:
                text = getattr(block, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    if content is None:
        return ""
    return str(content)
