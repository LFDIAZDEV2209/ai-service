"""Extracción de hechos del usuario a partir de sus mensajes.

Heurística determinista (sin LLM) para capturar declaraciones explícitas del
usuario — perfil, preferencias, datos clínicos y objetivos — y convertirlas en
memorias de largo plazo. No pretende entender lenguaje libre: solo patrones
declarativos claros. Los resúmenes de conversación (fase de summaries
rodantes) sí usan el LLM del agente (ver `app/memory/service.py`).

El aislamiento lo garantiza el llamador: los hechos siempre se guardan
asociados a un `user_id`/`agent_type_id` concreto (ver `UserMemoryStore`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Categorías de memoria (coinciden con `AgentMemory.category`).
CATEGORY_PERSONAL = "personal"
CATEGORY_PREFERENCIA = "preferencia"
CATEGORY_CLINICO = "clinico"
CATEGORY_OBJETIVO = "objetivo"
CATEGORY_RESUMEN = "resumen"

# Importancia por categoría: los datos clínicos y objetivos pesan más.
_IMPORTANCE = {
    CATEGORY_PERSONAL: 0.5,
    CATEGORY_PREFERENCIA: 0.4,
    CATEGORY_CLINICO: 0.9,
    CATEGORY_OBJETIVO: 0.7,
}


@dataclass(frozen=True)
class UserFact:
    """Hecho declarado por el usuario, listo para persistir como memoria."""

    content: str
    category: str
    importance: float = 0.5
    source: str = "chat"


@dataclass
class ExtractionResult:
    """Resultado de la extracción sobre un mensaje del usuario."""

    facts: list[UserFact] = field(default_factory=list)
    message: str = ""

    @property
    def has_facts(self) -> bool:
        return bool(self.facts)


# (patrón regex, categoría, importancia)
_PATTERNS: list[tuple[re.Pattern[str], str, float]] = [
    # --- Datos personales ---
    (
        # Sin IGNORECASE en el grupo del nombre: el prefijo admite mayúscula/
        # minúscula pero el nombre exige inicial mayúscula (evita capturar
        # "Ana otra vez" como nombre por case-insensitive).
        re.compile(
            r"\b(?:[Mm]e llamo|[Mm]i nombre es|[Ss]oy)\s+"
            r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){0,2})",
        ),
        CATEGORY_PERSONAL,
        _IMPORTANCE[CATEGORY_PERSONAL],
    ),
    (
        re.compile(r"\b[Tt]engo\s+(\d{1,3})\s*(?:años|años de edad)\b"),
        CATEGORY_PERSONAL,
        _IMPORTANCE[CATEGORY_PERSONAL],
    ),
    (
        re.compile(
            r"\b(?:[Vv]ivo en|[Vv]ivo cerca de|[Ss]oy de)\s+"
            r"([A-Za-záéíóúñÁÉÍÓÚÑ][\w\sáéíóúñÁÉÍÓÚÑ]{2,30}?)\b(?:\.|,|$)",
        ),
        CATEGORY_PERSONAL,
        _IMPORTANCE[CATEGORY_PERSONAL],
    ),
    # --- Preferencias ---
    (
        re.compile(r"\bme gusta(n)?\s+(.+?)\b(?:\.|,|$)", re.IGNORECASE),
        CATEGORY_PREFERENCIA,
        _IMPORTANCE[CATEGORY_PREFERENCIA],
    ),
    (
        re.compile(r"\bprefiero\s+(.+?)\b(?:\.|,|$)", re.IGNORECASE),
        CATEGORY_PREFERENCIA,
        _IMPORTANCE[CATEGORY_PREFERENCIA],
    ),
    (
        re.compile(r"\bno me gusta(n)?\s+(.+?)\b(?:\.|,|$)", re.IGNORECASE),
        CATEGORY_PREFERENCIA,
        _IMPORTANCE[CATEGORY_PREFERENCIA],
    ),
    # --- Datos clínicos ---
    (
        # Tolerante a tildes ("alergica" y "alérgica"): los usuarios a menudo
        # escriben sin acentos.
        re.compile(
            r"\b(?:soy al[eé]rgic[oa] a|tengo alergia a|me da alergia)\s+(.+?)\b(?:\.|,|$)",
            re.IGNORECASE,
        ),
        CATEGORY_CLINICO,
        _IMPORTANCE[CATEGORY_CLINICO],
    ),
    (
        re.compile(
            r"\b(?:padezco|sufro de|tengo)\s+(diabetes|hipertensión|hipertension|asma|epilepsia|"
            r"artritis|migraña|migrana|colesterol|anemia|insomnio)\b",
            re.IGNORECASE,
        ),
        CATEGORY_CLINICO,
        _IMPORTANCE[CATEGORY_CLINICO],
    ),
    (
        re.compile(r"\b(?:tomo|estoy tomando|me recetaron)\s+(.+?)\b(?:\.|,|$)", re.IGNORECASE),
        CATEGORY_CLINICO,
        _IMPORTANCE[CATEGORY_CLINICO],
    ),
    # --- Objetivos ---
    (
        re.compile(
            r"\b(?:quiero|me gustaría|mi objetivo es|busco)\s+(.+?)\b(?:\.|,|$)",
            re.IGNORECASE,
        ),
        CATEGORY_OBJETIVO,
        _IMPORTANCE[CATEGORY_OBJETIVO],
    ),
]


def _normalize(content: str) -> str:
    """Normaliza el hecho para deduplicación (minúsculas, sin puntuación final)."""
    return re.sub(r"[\.\,\;\s]+$", "", content.strip().lower())


def extract_facts(message: str) -> ExtractionResult:
    """Extrae hechos declarativos de un mensaje del usuario.

    Devuelve hechos únicos (deduplicados por contenido normalizado), en orden
    de aparición. Los patrones capturan la parte significativa sin incluir la
    frase introductoria ("me llamo X" → guarda "X").
    """
    result = ExtractionResult(message=message.strip())
    seen: set[str] = set()

    for pattern, category, importance in _PATTERNS:
        for match in pattern.finditer(message):
            capture = next(
                (g for g in match.groups() if g and g.strip()),
                match.group(0),
            )
            content = capture.strip()
            if not content or len(content) > 200:
                continue

            key = _normalize(content)
            if key in seen:
                continue
            seen.add(key)

            result.facts.append(
                UserFact(content=content, category=category, importance=importance)
            )

    return result
