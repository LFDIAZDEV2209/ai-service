"""Tests del enrutador de intenciones (keywords)."""

from __future__ import annotations

import pytest

from app.graph.state import AgentState
from app.orchestration.router import IntentRouter, _normalize

router = IntentRouter()


def _state(text: str) -> AgentState:
    return {"input": text}


@pytest.mark.parametrize(
    "mensaje,esperado",
    [
        ("¿Qué debo comer hoy?", "nutrition"),
        ("Necesito una dieta para bajar de peso", "nutrition"),
        ("Cuántas calorías tiene el arroz", "nutrition"),
        ("¿Me conviene ayunar?", "nutrition"),
        ("Me duele el pecho", "medical"),
        ("Tengo fiebre y vómitos", "medical"),
        ("¿Qué dosis de mi medicamento?", "medical"),
        ("Me siento muy ansioso", "psychology"),
        ("Tengo insomnio por estrés", "psychology"),
        ("Quiero terapia por depresión", "psychology"),
        ("Hola, ¿qué puedes hacer?", "base"),
        ("Cuéntame sobre la aplicación", "base"),
        ("", "base"),
    ],
)
def test_route_por_keywords(mensaje: str, esperado: str) -> None:
    assert router.route(_state(mensaje)) == esperado


def test_prioridad_medical_sobre_psicologia() -> None:
    # "dolor" (medical) debe ganar incluso si aparece "ansiedad" (psychology).
    assert router.route(_state("Siento dolor en el pecho y ansiedad")) == "medical"


def test_normalize_sin_acentos() -> None:
    # El matching debe funcionar con acentos (se normalizan).
    assert "nutricion" in _normalize("Necesito nutrición")


def test_keyword_de_dos_dominios_usa_prioridad() -> None:
    # "peso" puede ser nutrición; si hay señal clínica, gana medical.
    assert router.route(_state("Tengo dolor por la obesidad")) == "medical"


def test_saludable_no_roba_a_nutricion() -> None:
    # Falso positivo corregido: "saludable" contiene el prefijo "salud" (medical),
    # pero "comer"+"dieta" (nutrition) tienen más evidencia → gana nutrition.
    assert router.route(_state("que puedo comer hoy para una dieta saludable")) == "nutrition"
