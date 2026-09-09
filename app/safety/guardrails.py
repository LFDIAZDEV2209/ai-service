"""Guardrails de seguridad (patrón portado del curso node-dev-assistant).

- Sanitización de entrada (control chars, longitudes).
- Detección de prompt injection (inglés y español).
- Rate limiting por cliente.
- Gancho para validación de salida.
"""

import re
import time
from dataclasses import dataclass

from app.core.config import get_settings

# ── Sanitización ────────────────────────────────────────────────────────────


def sanitize_input(input_text: str, max_length: int | None = None) -> str:
    """Limpia el texto de entrada: null bytes, control chars y longitudes abusivas."""
    settings = get_settings()
    max_length = max_length or settings.max_input_length

    result = input_text.replace("\x00", "").replace("\r\n", "\n")
    # Quita control chars PERO conserva \n y \t (saltos de línea y tabs)
    result = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", result)
    result = re.sub(r"\n{3,}", "\n\n", result)

    if len(result) > max_length:
        result = result[:max_length] + "\n[excedió el límite — cortado]"
    return result


# ── Detección de prompt injection (EN + ES) ─────────────────────────────────

INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Inglés
    ("ignore instructions", re.compile(r"ignore\s+(?:\w+\s+){0,3}instructions?", re.I)),
    (
        "forget instructions",
        re.compile(
            r"forget\s+(everything|all|your\s+instructions?|what\s+you\s+were\s+told)", re.I
        ),
    ),
    ("you are now", re.compile(r"you\s+are\s+now\s+", re.I)),
    ("act as", re.compile(r"act\s+as\s+(if\s+)?you\s+(are|were)\s+", re.I)),
    ("disregard", re.compile(r"disregard\s+(your|all|previous|the)\s+", re.I)),
    ("new instructions", re.compile(r"new\s+instructions?\s*:", re.I)),
    ("system override", re.compile(r"system\s*:?\s*you\s+", re.I)),
    (
        "override system prompt",
        re.compile(r"override\s+(the\s+)?(system\s+prompt|your\s+instructions?)", re.I),
    ),
    # Español
    (
        "ignorar instrucciones (es)",
        re.compile(r"ignora\s+(las\s+)?(instrucciones?\s+)?(anteriores?|previas?|todas?)", re.I),
    ),
    (
        "olvida instrucciones (es)",
        re.compile(
            r"olvida\s+(todo|las\s+instrucciones?|lo\s+que\s+te\s+(dijeron|indicaron))", re.I
        ),
    ),
    ("ahora eres (es)", re.compile(r"ahora\s+(eres|serás|actúas?\s+como)\s+", re.I)),
    ("actúa como (es)", re.compile(r"act[uú]a\s+(como\s+si\s+)?(fueras?|eres)\s+", re.I)),
    ("nuevas instrucciones (es)", re.compile(r"nuevas?\s+instrucciones?\s*:", re.I)),
    ("ignora todo (es)", re.compile(r"ignora\s+todo\s+(lo\s+anterior|lo\s+que\s+)", re.I)),
    ("eres libre (es)", re.compile(r"eres\s+libre\s+(de|para)\s+", re.I)),
    (
        "sin restricciones (es)",
        re.compile(r"sin\s+(ninguna\s+)?(restricci[oó]n|l[ií]mite|instrucci[oó]n)", re.I),
    ),
]


def detect_prompt_injection(text: str) -> tuple[bool, str | None]:
    """Devuelve (detectado, nombre_del_patrón)."""
    for name, pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            return True, name
    return False, None


# ── Rate limiting ────────────────────────────────────────────────────────────


class RateLimiter:
    """Ventana deslizante de peticiones (portado del curso, adaptado a pydantic)."""

    def __init__(self, max_requests: int | None = None, window_seconds: int | None = None):
        settings = get_settings()
        self.max_requests = max_requests or settings.rate_limit_max_requests
        self.window_seconds = window_seconds or settings.rate_limit_window_seconds
        self._timestamps: list[float] = []

    def check(self) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        self._timestamps = [ts for ts in self._timestamps if ts > cutoff]
        if len(self._timestamps) >= self.max_requests:
            return False
        self._timestamps.append(now)
        return True

    @property
    def remaining(self) -> int:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        active = sum(1 for ts in self._timestamps if ts > cutoff)
        return max(0, self.max_requests - active)

    def reset(self) -> None:
        self._timestamps = []


# ── Puntos de entrada/salida del grafo ──────────────────────────────────────


@dataclass
class GuardrailResult:
    safe: bool
    sanitized: str
    reason: str | None = None
    pattern: str | None = None


def check_input_guardrails(raw_input: str) -> GuardrailResult:
    """Valida y sanitiza el mensaje del usuario (nodo de entrada del grafo)."""
    sanitized = sanitize_input(raw_input)

    if not sanitized.strip():
        return GuardrailResult(safe=False, sanitized=sanitized, reason="Mensaje vacío.")

    detected, pattern = detect_prompt_injection(sanitized)
    if detected:
        return GuardrailResult(
            safe=False,
            sanitized=sanitized,
            reason=(
                "Tu mensaje contiene patrones que intentan modificar el comportamiento "
                "del asistente. Por favor reformula tu pregunta."
            ),
            pattern=pattern,
        )
    return GuardrailResult(safe=True, sanitized=sanitized)


def check_output_guardrails(text: str, max_length: int = 120_000) -> str:
    """Valida la salida del agente antes de enviarla al cliente (nodo de salida)."""
    if len(text) > max_length:
        text = text[:max_length] + "\n[respuesta truncada por longitud]"
    return text
