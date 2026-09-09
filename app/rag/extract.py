"""Extracción de texto de documentos para ingestión RAG.

Soporta markdown/texto plano y PDF (via pypdf). El resultado alimenta el
chunker; el archivo original nunca se almacena en el AI Service (el backend
conserva el blob en su storage de objetos).
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.errors import CoppAiError

logger = logging.getLogger(__name__)

_TEXT_EXTENSIONS = {".md", ".markdown", ".txt", ".text", ".rst"}

MAX_DOCUMENT_CHARS = 2_000_000  # ~500k tokens; límite de sanidad por documento


class DocumentExtractionError(CoppAiError):
    """No se pudo extraer texto del documento."""


def extract_text(filename: str, content: bytes) -> str:
    """Extrae el texto de un documento según su extensión.

    Args:
        filename: nombre del archivo (la extensión decide el parser).
        content: bytes del archivo.

    Returns:
        Texto extraído (UTF-8).

    Raises:
        DocumentExtractionError: si la extensión no es soportada o el
            documento no se pudo parsear.
    """
    suffix = Path(filename).suffix.lower()
    if suffix in _TEXT_EXTENSIONS:
        return _extract_text_plain(content)
    if suffix == ".pdf":
        return _extract_pdf(content)
    raise DocumentExtractionError(
        f"Tipo de documento no soportado: '{suffix or '(sin extensión)'}'. "
        "Soportados: .md, .markdown, .txt, .rst, .pdf"
    )


def _extract_text_plain(content: bytes) -> str:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        # Fallback: latin-1 nunca falla; si el doc es otro encoding UTF-8
        # legítimo, se degrada a caracteres de reemplazo.
        text = content.decode("utf-8", errors="replace")
    if len(text) > MAX_DOCUMENT_CHARS:
        raise DocumentExtractionError(
            f"Documento demasiado grande: {len(text)} caracteres (máximo {MAX_DOCUMENT_CHARS})."
        )
    return text


def _extract_pdf(content: bytes) -> str:
    try:
        import io

        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependencia opcional
        raise DocumentExtractionError(
            "pypdf no está instalado; no se pueden indexar PDFs."
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(content))
        pages: list[str] = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
    except Exception as exc:
        logger.warning("Error parseando PDF: %s", exc)
        raise DocumentExtractionError(f"No se pudo extraer texto del PDF: {exc}") from exc

    text = "\n\n".join(pages).strip()
    if not text:
        raise DocumentExtractionError("El PDF no contiene texto extraíble (¿está escaneado?).")
    if len(text) > MAX_DOCUMENT_CHARS:
        raise DocumentExtractionError(
            f"Documento demasiado grande: {len(text)} caracteres (máximo {MAX_DOCUMENT_CHARS})."
        )
    return text
