"""Enrutamiento de intenciones del supervisor.

El supervisor decide qué agente/subgrafo atiende cada mensaje según la
intención. El routing es POR MENSAJE: cada turno re-evalúa el `input` actual,
por lo que el perfil puede cambiar de un mensaje al siguiente dentro del
mismo thread.

Mecanismo activo: clasificación por keywords (barata, determinista). Queda
implementado pero comentado un clasificador por LLM para activarlo más tarde
si las keywords se quedan cortas.
"""

from __future__ import annotations

from app.graph.state import AgentState

# Palabras clave por dominio (minúsculas, sin acentos). El matching es por
# PREFIJO de palabra: una keyword matchea si un token del input EMPIEZA con
# ella. Esto evita falsos positivos por substring (ej. "depresión" NO debe
# activar "presión" de medical porque el token "depresion" no empieza con
# "presion"). Usar raíces cortas: "ayun" cubre "ayunar"/"ayuno".
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "nutrition": (
        "comer", "comida", "diet", "calor", "kcal", "aliment", "menu", "recet",
        "porcion", "ayun", "glucos", "vitamin", "imc", "nutricion", "protein",
        "carbohidr", "nutrient", "peso",
    ),
    "medical": (
        "dolor", "dolie", "dole", "sintoma", "medicament", "dosis", "diagnost",
        "presion", "fiebre", "condicion", "tratamiento", "salud", "enfermed",
        "medico", "vomito", "mareo", "palpitac", "dificultad", "urgencia",
        "pecho", "respir", "dolor de pecho",
    ),
    "psychology": (
        "ansied", "ansios", "estres", "depresi", "trist", "mied", "insomni",
        "animo", "terapia", "psicolog", "panico", "nervi", "angusti",
        "emocional", "salud mental", "bienestar emocional", "mindfulness",
    ),
}

# Orden de prioridad si un mensaje matchea keywords de varios dominios: la
# salud clínica gana sobre la psicológica y la nutrición para evitar que
# términos superpuestos (ej. "peso" / "dolor" emocional) caigan en el perfil
# equivocado.
_PRIORITY: tuple[str, ...] = ("medical", "psychology", "nutrition")


def _normalize(text: str) -> str:
    """Normaliza el input para el matching: minúsculas y sin acentos."""
    import unicodedata

    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def _tokens(text: str) -> list[str]:
    """Separa el texto en palabras (tokens alfabéticos)."""
    import re

    return [t for t in re.split(r"[^a-z0-9]+", text) if t]


def _score_domain(text: str, keywords: tuple[str, ...]) -> int:
    """Cuenta cuántas keywords del dominio matchean el texto (prefijo de token).

    Mayor cantidad de señales = mayor confianza en el dominio. Esto evita que
    un prefijo amplio (ej. "salud" en "saludable") gane solo contra un dominio
    con más evidencia ("comer"+"dieta" de nutrition).
    """
    tokens = _tokens(text)
    return sum(
        1 for kw in keywords if kw in text and any(tok.startswith(kw) for tok in tokens)
    )


class IntentRouter:
    """Selecciona el perfil de agente que atiende cada mensaje."""

    def route(self, state: AgentState) -> str:
        """Devuelve la clave del perfil de agente (ver agents/registry.py).

        Gana el dominio con MÁS keywords matcheadas (más evidencia). Ante
        empate, gana el de mayor prioridad en `_PRIORITY`.
        """
        text = _normalize(state.get("input", ""))
        if not text:
            return "base"

        best: str | None = None
        best_score = 0
        for domain in _PRIORITY:
            score = _score_domain(text, _KEYWORDS.get(domain, ()))
            # `>` (no `>=`) hace que, ante empate, gane el de mayor prioridad
            # (medical evaluado primero).
            if score > best_score:
                best_score = score
                best = domain

        return best if best_score > 0 else "base"

    # ── Clasificador por LLM (DESACTIVADO) ──────────────────────────────
    # TODO(activar): si las keywords se quedan cortas ante vocabulario
    # variado, usar este clasificador ANTES del matching por keywords.
    # Requiere pasar un modelo al router o resolverlo vía `get_chat_model`.
    # Pseudo-implementación de referencia:
    #
    # async def _classify_with_llm(self, text: str) -> str:
    #     """Pregunta al LLM qué dominio es: nutrition | medical | psychology | base."""
    #     from app.llm.factory import get_chat_model
    #     from langchain_core.messages import HumanMessage, SystemMessage
    #     model = get_chat_model()
    #     prompt = [
    #         SystemMessage(
    #             content=(
    #                 "Clasifica el mensaje del usuario en UNO de estos 3 dominios: "
    #                 "nutrition | medical | psychology. "
    #                 "Respondé SOLO con la etiqueta, sin texto adicional. "
    #                 "Si no corresponde a ninguno, respondé 'base'."
    #             )
    #         ),
    #         HumanMessage(content=text),
    #     ]
    #     resp = await model.ainvoke(prompt)
    #     tag = (resp.content or "").strip().lower()
    #     domains = set(_KEYWORDS) | {"base"}
    #     return tag if tag in domains else "base"


router = IntentRouter()
