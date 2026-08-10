"""División de documentos en chunks por encabezados (patrón del curso).

Mantiene el contexto estructural (título → sección) para que la recuperación
sea semántica y no pierda el anclaje del contenido.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")


@dataclass
class Chunk:
    id: str
    content: str
    metadata: dict = field(default_factory=dict)


def _chunk_id(source: str, heading: str, index: int) -> str:
    raw = f"{source}::{heading}::{index}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def chunk_markdown(
    text: str,
    source: str,
    max_chars: int = 1500,
) -> list[Chunk]:
    """Divide texto markdown en chunks agrupados por encabezado.

    Args:
        text: contenido markdown completo.
        source: identificador del documento (ruta o título) para metadata.
        max_chars: tamaño máximo aproximado por chunk.
    """
    chunks: list[Chunk] = []
    current_heading = "Sin sección"
    buffer: list[str] = []
    buffer_len = 0
    position = 0

    def flush() -> None:
        nonlocal buffer, buffer_len
        if not buffer:
            return
        content = "\n".join(buffer).strip()
        if content:
            chunks.append(
                Chunk(
                    id=_chunk_id(source, current_heading, len(chunks)),
                    content=content,
                    metadata={
                        "source": source,
                        "heading": current_heading,
                        "position": position,
                        "char_count": len(content),
                    },
                )
            )
        buffer, buffer_len = [], 0

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        heading_match = HEADING_RE.match(line)

        if heading_match:
            flush()
            current_heading = heading_match.group(2).strip()

        buffer.append(raw_line)
        buffer_len += len(line) + 1

        if buffer_len >= max_chars and not heading_match:
            flush()
            position += 1

    flush()
    return chunks
