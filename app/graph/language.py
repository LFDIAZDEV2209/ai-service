"""Detección determinista del idioma del mensaje actual, sin memoria entre turnos."""

import re
import unicodedata
from typing import Literal

from app.graph.state import AgentState

_EN_MARKERS = frozenset(
    [
        "i",
        "you",
        "your",
        "yours",
        "my",
        "mine",
        "we",
        "our",
        "they",
        "their",
        "the",
        "this",
        "that",
        "these",
        "those",
        "is",
        "are",
        "am",
        "was",
        "were",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "can",
        "could",
        "would",
        "should",
        "what",
        "how",
        "why",
        "when",
        "where",
        "which",
        "please",
        "with",
        "for",
        "and",
        "of",
        "to",
        "in",
        "feel",
        "feeling",
        "need",
        "help",
    ]
)
_ES_MARKERS = frozenset(
    [
        "yo",
        "tú",
        "tu",
        "tus",
        "usted",
        "ustedes",
        "mi",
        "mis",
        "nosotros",
        "nuestro",
        "ellos",
        "ellas",
        "el",
        "la",
        "los",
        "las",
        "un",
        "una",
        "unos",
        "unas",
        "es",
        "son",
        "soy",
        "estoy",
        "tengo",
        "tiene",
        "siento",
        "necesito",
        "quiero",
        "puedo",
        "puedes",
        "qué",
        "que",
        "cómo",
        "como",
        "cuándo",
        "cuando",
        "dónde",
        "donde",
        "cuál",
        "cual",
        "por",
        "para",
        "con",
        "de",
        "del",
        "en",
        "y",
        "gracias",
        "ayuda",
    ]
)


def detect_turn_language(state: AgentState) -> Literal["en", "es"] | None:
    """Devuelve un idioma claro; las señales mixtas o débiles conservan el de la app."""
    text = unicodedata.normalize("NFC", state.get("input", "")).lower()
    # Un título citado en otro idioma no cambia el idioma de la conversación.
    text = re.sub(
        r'"[^"\n]{2,180}"|“[^”\n]{2,180}”|(?<!\w)\x27[^\x27\n]{2,180}\x27(?!\w)', " ", text
    )
    preference = re.search(
        r"\b(?:h[aá]bla(?:me)?|responde(?:me)?|contesta(?:me)?|speak|answer|respond)\s+"
        r"(?:(?:to me|en|in)\s+)*(ingl[eé]s|english|espanol|español|spanish)\b",
        text,
    )
    if preference:
        return "en" if preference[1] in {"ingles", "inglés", "english"} else "es"
    tokens = re.findall(r"[^\W\d_]+", text)
    if not tokens:
        return None
    en = sum(token in _EN_MARKERS for token in tokens)
    es = sum(token in _ES_MARKERS for token in tokens)
    accented = any(char in text for char in "áéíóúüñ¿¡")
    # Spanglish no fuerza un cambio, incluso si uno de los idiomas supera el umbral.
    if en and (es or accented):
        return None
    if accented or es >= 2 or es / len(tokens) >= 0.4:
        return "es"
    if en >= 2 or en / len(tokens) >= 0.4:
        return "en"
    return None
