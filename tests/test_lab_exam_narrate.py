"""Tests del endpoint de narración empática (POST /chat/lab-exam/narrate).

Cubre R4 (language), R5 (contenido), R6 (tier/brevity), R7 (contrato de
respuesta), R8 (degradación) y R9 (efimeridad — verificado por ausencia: la
ruta no toca la BD ni el checkpointer).
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.api.deps import get_db_session
from app.api.main import create_app
from app.api.routes.lab_exam import EMPATHETIC_PROMPT, get_empathetic_model
from app.core.config import get_settings
from app.core.errors import ConfigError

INTERNAL_KEY = get_settings().internal_api_key
HEADERS = {"X-Internal-Key": INTERNAL_KEY}

NARRATE_PAYLOAD = {
    "metrics": [
        {
            "metric_name": "glucose_fasting",
            "value": 120.0,
            "unit_symbol": "mg/dL",
            "observed_at": "2026-09-10T09:00:00Z",
        }
    ],
    "previous_measurements": {
        "glucose_fasting": {
            "direction": "worsened",
            "previous_value": 100.0,
            "previous_unit": "mg/dL",
            "previous_date": "2026-07-01T10:00:00Z",
            "delta": 20.0,
            "current_value": 120.0,
            "current_unit": "mg/dL",
        }
    },
    "language": "es",
}


class FakeDbSession:
    """Sesión de BD fake: el endpoint de narración NUNCA debe usarla."""

    async def add(self, *args, **kwargs):
        raise AssertionError("narrate must not write to the database")

    async def commit(self):
        raise AssertionError("narrate must not write to the database")


class ExplodingDbSession:
    """Sesión que explota al instanciarse: prueba la ausencia de uso de BD."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("narrate must not open a database session (no ai. writes)")


class FakeEmphaticModel:
    """Modelo fake que captura los mensajes para inspeccionar el prompt."""

    def __init__(self, response_text: str):
        self.response_text = response_text
        self.invoked_with: list[list] = []

    async def ainvoke(self, messages, *args, **kwargs):
        self.invoked_with.append(messages)
        return AIMessage(content=self.response_text)


class RaisingModel:
    async def ainvoke(self, *args, **kwargs):
        raise RuntimeError("LLM service unavailable")


class SlowModel:
    async def ainvoke(self, *args, **kwargs):
        await asyncio.sleep(5)
        return AIMessage(content="never returned")


class FakeSettings:
    """Settings con timeout corto para probar degradación por timeout."""

    empathetic_timeout: float = 0.05


@pytest.fixture
def test_app():
    app = create_app()
    app.dependency_overrides[get_db_session] = FakeDbSession
    return app


def _system_prompt(fake: FakeEmphaticModel) -> str:
    """Extrae el SystemMessage (prompt construido) capturado por el modelo fake."""
    assert fake.invoked_with, "el modelo nunca fue invocado"
    return fake.invoked_with[0][0].content


# --- R7: contrato de respuesta -------------------------------------------------


def test_narrate_success_returns_empathetic_message(test_app):
    """Narración exitosa retorna 200 con el mensaje empático."""
    fake = FakeEmphaticModel("¡Hola! Recibí tu examen. Tu glucosa subió.")
    test_app.dependency_overrides[get_empathetic_model] = lambda: fake

    with TestClient(test_app) as client:
        res = client.post("/chat/lab-exam/narrate", headers=HEADERS, json=NARRATE_PAYLOAD)
        assert res.status_code == 200
        assert res.json() == {"empathetic_message": "¡Hola! Recibí tu examen. Tu glucosa subió."}


def test_narrate_requires_internal_key(test_app):
    """Rechaza requests sin X-Internal-Key con 401."""
    with TestClient(test_app) as client:
        res = client.post("/chat/lab-exam/narrate", json=NARRATE_PAYLOAD)
        assert res.status_code == 401


def test_narrate_via_api_v1_prefix(test_app):
    """El endpoint también es alcanzable bajo /api/v1/chat/lab-exam/narrate."""
    fake = FakeEmphaticModel("Hola")
    test_app.dependency_overrides[get_empathetic_model] = lambda: fake

    with TestClient(test_app) as client:
        res = client.post("/api/v1/chat/lab-exam/narrate", headers=HEADERS, json=NARRATE_PAYLOAD)
        assert res.status_code == 200
        assert res.json()["empathetic_message"] == "Hola"


# --- R2/R5: deltas verbatim en el prompt (sin aritmética) ----------------------


def test_prompt_contains_verbatim_delta_and_unit(test_app):
    """El delta calculado por el backend llega verbatim al prompt (+20 mg/dL)."""
    fake = FakeEmphaticModel("ok")
    test_app.dependency_overrides[get_empathetic_model] = lambda: fake

    with TestClient(test_app) as client:
        client.post("/chat/lab-exam/narrate", headers=HEADERS, json=NARRATE_PAYLOAD)

    prompt = _system_prompt(fake)
    assert "+20 mg/dL" in prompt
    # La aritmética ya la hizo el backend: el prompt prohíbe recalcular (R2).
    assert "do NOT calculate" in prompt


def test_first_record_metric_rendered_without_previous_value(test_app):
    """Un first_record se presenta sin valor previo ni comparación."""
    payload = {
        "metrics": [{"metric_name": "hba1c", "value": 5.4, "unit_symbol": "%"}],
        "previous_measurements": {"hba1c": {"direction": "first_record"}},
        "language": "es",
    }
    fake = FakeEmphaticModel("ok")
    test_app.dependency_overrides[get_empathetic_model] = lambda: fake

    with TestClient(test_app) as client:
        client.post("/chat/lab-exam/narrate", headers=HEADERS, json=payload)

    prompt = _system_prompt(fake)
    assert "first_record" in prompt
    assert "no previous measurement" in prompt


# --- R5: estructura, prohibiciones y brevity -----------------------------------


def test_prompt_instructs_structure_opener_highlight_close():
    """El prompt instruye: opener de confirmación, 3-4 métricas destacadas y cierre."""
    assert "confirmation opener" in EMPATHETIC_PROMPT
    assert "3 to 4" in EMPATHETIC_PROMPT
    assert "encouraging close" in EMPATHETIC_PROMPT


def test_prompt_instructs_brevity_cap():
    """El prompt instruye brevedad (~150 palabras)."""
    assert "150 words" in EMPATHETIC_PROMPT


def test_prompt_prohibits_diagnoses_medication_alarmism():
    """El prompt prohíbe diagnósticos, consejos de medicación y alarmismo."""
    assert "diagnoses" in EMPATHETIC_PROMPT
    assert "medication" in EMPATHETIC_PROMPT
    assert "alarmist" in EMPATHETIC_PROMPT


def test_prompt_forbids_raw_reference_ranges():
    """El prompt prohíbe rangos de referencia numéricos."""
    assert "reference ranges" in EMPATHETIC_PROMPT


def test_prompt_suggests_doctor_review_for_worsened():
    """Con métricas worsened, el prompt instruye sugerir consultar al médico."""
    assert "worsened" in EMPATHETIC_PROMPT
    assert "doctor" in EMPATHETIC_PROMPT


# --- R4: idioma -----------------------------------------------------------------


def test_prompt_language_en_instructs_english(test_app):
    """language='en' ⇒ el prompt instruye salida en inglés."""
    fake = FakeEmphaticModel("ok")
    test_app.dependency_overrides[get_empathetic_model] = lambda: fake
    payload = {**NARRATE_PAYLOAD, "language": "en"}

    with TestClient(test_app) as client:
        client.post("/chat/lab-exam/narrate", headers=HEADERS, json=payload)

    assert "Write the message in English" in _system_prompt(fake)


def test_prompt_missing_language_defaults_spanish(test_app):
    """language ausente ⇒ el prompt instruye salida en español."""
    fake = FakeEmphaticModel("ok")
    test_app.dependency_overrides[get_empathetic_model] = lambda: fake
    payload = {k: v for k, v in NARRATE_PAYLOAD.items() if k != "language"}

    with TestClient(test_app) as client:
        client.post("/chat/lab-exam/narrate", headers=HEADERS, json=payload)

    assert "Write the message in Spanish" in _system_prompt(fake)


def test_prompt_unsupported_language_defaults_spanish(test_app):
    """language='fr' (no soportado) ⇒ el prompt instruye salida en español."""
    fake = FakeEmphaticModel("ok")
    test_app.dependency_overrides[get_empathetic_model] = lambda: fake
    payload = {**NARRATE_PAYLOAD, "language": "fr"}

    with TestClient(test_app) as client:
        client.post("/chat/lab-exam/narrate", headers=HEADERS, json=payload)

    assert "Write the message in Spanish" in _system_prompt(fake)


# --- R8: degradación (nunca 5xx) -----------------------------------------------


def test_model_exception_returns_200_empty(test_app):
    """Excepción del modelo ⇒ HTTP 200 con empathetic_message vacío."""
    test_app.dependency_overrides[get_empathetic_model] = lambda: RaisingModel()

    with TestClient(test_app) as client:
        res = client.post("/chat/lab-exam/narrate", headers=HEADERS, json=NARRATE_PAYLOAD)
        assert res.status_code == 200
        assert res.json() == {"empathetic_message": ""}


def test_model_timeout_returns_200_empty(test_app, monkeypatch):
    """Timeout del modelo ⇒ HTTP 200 con empathetic_message vacío."""
    test_app.dependency_overrides[get_empathetic_model] = lambda: SlowModel()
    monkeypatch.setattr("app.api.routes.lab_exam.get_settings", lambda: FakeSettings())

    with TestClient(test_app) as client:
        res = client.post("/chat/lab-exam/narrate", headers=HEADERS, json=NARRATE_PAYLOAD)
        assert res.status_code == 200
        assert res.json() == {"empathetic_message": ""}


def test_config_error_returns_200_empty(test_app, monkeypatch):
    """Configuración inválida (sin OPENAI_API_KEY) ⇒ 200 con mensaje vacío."""
    monkeypatch.setattr(
        "app.api.routes.lab_exam.get_chat_model",
        lambda **kwargs: (_ for _ in ()).throw(ConfigError("OPENAI_API_KEY no está configurada")),
    )

    with TestClient(test_app) as client:
        res = client.post("/chat/lab-exam/narrate", headers=HEADERS, json=NARRATE_PAYLOAD)
        assert res.status_code == 200
        assert res.json() == {"empathetic_message": ""}


# --- R9: efimeridad — verificado por ausencia ----------------------------------


def test_narrate_never_touches_database(test_app):
    """La narración no abre sesión de BD ni escribe en schema ai. (por ausencia)."""
    test_app.dependency_overrides[get_db_session] = ExplodingDbSession
    fake = FakeEmphaticModel("Hola")
    test_app.dependency_overrides[get_empathetic_model] = lambda: fake

    with TestClient(test_app) as client:
        res = client.post("/chat/lab-exam/narrate", headers=HEADERS, json=NARRATE_PAYLOAD)
        assert res.status_code == 200
        assert res.json()["empathetic_message"] == "Hola"
