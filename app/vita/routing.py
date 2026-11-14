"""Selección acotada al catálogo autorizado del turno. Metadata no concede tools."""

import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFD", value.casefold())
    return "".join(c for c in value if unicodedata.category(c) != "Mn")


class RoutingMetadata(BaseModel):
    topics: list[str] = Field(default_factory=list, max_length=32)
    keywords: list[str] = Field(default_factory=list, max_length=32)
    channels: list[Literal["text", "voice"]] = Field(default_factory=list)


class AuthorizedAgent(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)
    id: str = Field(min_length=1)
    runtime_key: str = Field(min_length=1)
    slug: str | None = None
    name: str
    description: str | None = None
    specialty: str | None = None
    version_id: str = Field(min_length=1)
    version_number: int = Field(ge=1)
    routing: RoutingMetadata


@dataclass(frozen=True)
class RouteDecision:
    agent: AuthorizedAgent | None
    reason: str


Classifier = Callable[[str, list[AuthorizedAgent]], Awaitable[str | None]]


def _positive_text(value: str) -> str:
    # Evita que «no quiero nutrición, quiero ...» active el dominio negado.
    return re.sub(
        r"\b(?:no|not|don't|do not)\s+(?:(?:quiero|want|necesito|need)\s+)?[^,;.!?]+",
        "",
        normalize(value),
    )


def _service_query(text: str) -> bool:
    text = text.strip("¿?! .,;")
    arithmetic = re.sub(r"^(?:cuanto es|calcula|calculate|what is|how much is)\s+", "", text)
    arithmetic = re.sub(r"\s+(?:please|por favor)$", "", arithmetic)
    arithmetic = re.sub(
        r"\b(?:por|mas|menos|dividido entre|plus|minus|times|divided by)\b", "+", arithmetic
    )
    if re.search(r"\d", arithmetic) and re.fullmatch(r"[\d\s.,()+*/%=^\-]+", arithmetic):
        return True
    if re.fullmatch(
        r"(?:hola|hello|hi|hey|buenos dias|buenas tardes|good morning|good evening)"
        r"(?: vita)?[ ,.!]*(?:(?:como estas|how are you)(?: doing)?(?: hoy| today)?)?",
        text,
    ):
        return True
    if re.search(r"\b(quien eres|who are you|tu nombre|your name)\b", text):
        return True
    if re.match(r"^(?:gracias|thank you|thanks)\b", text) and not re.search(
        r"\b(pero|but|ahora|now|also)\b", text
    ):
        return True
    return bool(
        re.search(r"\b(app|aplicacion|application)\b", text)
        and re.search(
            r"\b(ayuda|usar|uso|utilizar|abro|abrir|help|use|using|open|settings)\b", text
        )
    )


async def select_agent(
    text: str,
    catalog: list[AuthorizedAgent],
    *,
    channel: str = "text",
    explicit: str | None = None,
    previous: str | None = None,
    classifier: Classifier | None = None,
) -> RouteDecision:
    allowed = [agent for agent in catalog if channel in agent.routing.channels]
    if explicit and explicit != "base":
        matches = [a for a in allowed if explicit in {a.id, a.runtime_key, a.slug}]
        if not matches:
            raise ValueError("agent_not_authorized")
        return RouteDecision(matches[0], "explicit")
    normalized = _positive_text(text)
    # Saludos/identidad no consultan un segundo modelo ni heredan especialista.
    if re.fullmatch(r"[\W]*(?:hola|hello|hi|hey|quien eres|who are you)[\W]*", normalized):
        return RouteDecision(None, "general")
    if _service_query(normalized):
        return RouteDecision(None, "service_fast_path")
    scored: list[tuple[int, AuthorizedAgent]] = []
    for agent in allowed:
        terms = [
            agent.name,
            agent.specialty or "",
            agent.slug or "",
            *agent.routing.topics,
            *agent.routing.keywords,
        ]
        score = sum(
            1
            for term in set(map(normalize, terms))
            if term and re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", normalized)
        )
        if score:
            scored.append((score, agent))
    scored.sort(key=lambda item: (-item[0], item[1].id))
    if scored and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return RouteDecision(scored[0][1], "catalog_rule")
    if (
        not scored
        and previous
        and re.match(
            r"^(?:y eso|y como|y que|y entonces|and (?:that|how|what)|how (?:do|can) i|eso)\b",
            normalized.strip("¿?! ."),
        )
    ):
        active = next((a for a in allowed if a.id == previous), None)
        if active:
            return RouteDecision(active, "continuity")
    nonliteral = (
        not scored
        and len(normalized.split()) >= 3
        and bool(
            re.search(
                r"[¿?]|\b(me|mi|mis|tengo|siento|estoy|necesito|quiero|busco|quisiera|puedo|"
                r"que|como|sobre|explica|explicame|orienta|my|i|feel|feeling|need|want|looking|"
                r"about|explain|tell|what|how|why)\b",
                normalized,
            )
        )
    )
    if classifier and allowed and (len(scored) > 1 or nonliteral):
        candidates = [a for _, a in scored] if scored else allowed
        selected = await classifier(text, candidates)
        active = next((a for a in candidates if a.id == selected), None)
        if active:
            return RouteDecision(active, "structured_classifier")
    return RouteDecision(None, "general" if not scored else "clarification")


def documentation_query(text: str) -> bool:
    """Intención documental ES/EN; no confunde un test personal con su protocolo."""
    return bool(
        re.search(
            r"\b(documentacion|documentos?|documentation|documents?|manual|manuales|"
            r"protocolo|protocolos|protocols?|guia|guias|guidelines?|fuentes?|sources?|"
            r"bibliografia|bibliography|rag|material (?:cargado|adjunto)|uploaded|attached)\b",
            _positive_text(text),
        )
    )


def service_query(text: str) -> bool:
    """Servicio/saludo sin lectura de ficha ni recuperación documental."""
    return _service_query(normalize(text))


def capability_scope(text: str) -> str | None:
    """Atajos a lecturas; los datos reales y sus permisos siempre vienen de BACK."""
    text = _positive_text(text)
    # Una pregunta conceptual/documental nunca se convierte en una lectura
    # personal por contener «tests», «ejercicios» o «rutina».
    personal = bool(
        re.search(
            r"\b(mi|mis|me|my|mine|tengo|asignad[oa]s?|assigned|hoy|today|pendientes?|"
            r"pending|para hoy|for today|que puedo|what can i)\b",
            text,
        )
    )
    if documentation_query(text) and not (
        personal and re.search(r"\b(plan|menu|almuerzo|lunch|podcast)\b", text)
    ):
        return None
    if not personal and not re.search(
        r"\b(abre|abrir|muestra|muestrame|ver|pon|reproduce|open|show|play|tasks|tareas)\b",
        text,
    ):
        return None
    patterns = {
        "nutrition": r"\b(almuerzo|desayuno|cena|comida|menu|meal|lunch|breakfast|"
        r"plan nutricional|alimentacion|mi dieta|my diet|nutrition plan|food plan|eating plan)\b",
        "assessments": r"\b(test|tests|pruebas|evaluaciones|assessment|assessments)\b",
        "podcast": r"\b(podcast|podcasts)\b",
        "exercise": r"\b(ejercicio|rutina|routine|workout|exercise|descanso|rest day|"
        r"entreno|entrenamiento|training)\b",
        "progress": r"\b(progreso|racha|xp|progress|streak|pasos|steps)\b",
        "day": r"\b(para hoy|tengo hoy|my day|for today|tareas|tasks)\b",
    }
    return next((scope for scope, pattern in patterns.items() if re.search(pattern, text)), None)
