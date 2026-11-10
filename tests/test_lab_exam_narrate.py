"""Texto factual sin LLM, cálculo clínico ni persistencia; transporte interno real de test."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db_session
from app.api.main import create_app
from app.core.config import get_settings

HEADERS = {"X-Internal-Key": get_settings().internal_api_key}
URL = "/chat/lab-exam/narrate"
PAYLOAD = {
    "metrics": [{"metric_name": "glucose_fasting", "value": 120, "unit_symbol": "mg/dL"}],
    "previous_measurements": {
        "glucose_fasting": {
            "direction": "changed",
            "change": "increase",
            "previous_value": 100,
            "previous_unit": "mg/dL",
            "previous_date": "2026-07-01T10:00:00Z",
            "previous_source": "patient",
            "current_value": 120,
            "current_unit": "mg/dL",
            "current_date": "2026-09-10T09:00:00Z",
            "current_source": "professional",
            "delta": 20,
        }
    },
}


@pytest.fixture
def client(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("narration must not open DB or invoke a provider")

    monkeypatch.setattr("app.api.routes.lab_exam.get_chat_model", forbidden)
    app = create_app()
    app.dependency_overrides[get_db_session] = forbidden
    with TestClient(app) as client:
        yield client


def _text(client, payload=None, url=URL):
    response = client.post(url, headers=HEADERS, json=payload or PAYLOAD)
    assert response.status_code == 200
    assert set(response.json()) == {"empathetic_message"}
    return response.json()["empathetic_message"]


def test_requires_internal_authentication(client):
    assert client.post(URL, json=PAYLOAD).status_code == 401


@pytest.mark.parametrize("language", ["es", "en"])
def test_factual_numbers_dates_units_and_neutral_origin(client, language):
    text = _text(client, {**PAYLOAD, "language": language})
    assert "120 mg/dL" in text and "100 mg/dL" in text and "+20 mg/dL" in text
    assert "2026-09-10" in text and "2026-07-01" in text
    if language == "en":
        assert "numerical increase" in text
        assert "professional record" in text and "patient record" in text
        assert "does not establish improvement or worsening" in text
    else:
        assert "aumento numérico" in text
        assert "registro profesional" in text and "registro del paciente" in text
        assert "no establece mejoría ni empeoramiento" in text


@pytest.mark.parametrize("language", [None, "fr", "ES"])
def test_legacy_language_fallback_and_api_prefix(client, language):
    assert "Valores registrados" in _text(
        client, {**PAYLOAD, "language": language}, "/api/v1" + URL
    )


@pytest.mark.parametrize("direction", ["improved", "worsened"])
def test_legacy_clinical_direction_does_not_assign_benefit(client, direction):
    payload = deepcopy(PAYLOAD)
    previous = payload["previous_measurements"]["glucose_fasting"]
    previous["direction"] = direction
    del previous["change"]
    text = _text(client, payload)
    assert "No se estableció un cambio comparable" in text
    assert direction not in text
    assert "+20" not in text


@pytest.mark.parametrize(
    "change,delta,current,word",
    [("decrease", -10, 90, "disminución numérica"), ("unchanged", 0, 100, "sin cambio numérico")],
)
def test_numeric_decrease_and_unchanged_remain_nonclinical(client, change, delta, current, word):
    payload = deepcopy(PAYLOAD)
    payload["previous_measurements"]["glucose_fasting"].update(
        change=change, delta=delta, current_value=current
    )
    assert word in _text(client, payload)


@pytest.mark.parametrize(
    "updates",
    [
        {"current_unit": "mmol/L"},
        {"previous_unit": None},
        {"current_date": None},
        {"previous_date": None},
        {"current_date": "2026-01-01T00:00:00Z"},
        {"current_date": "2026-09-10T00:00:00"},
        {"delta": 9},
        {"change": "decrease"},
        {"change": "not_comparable", "delta": None},
    ],
)
def test_uncertain_or_inconsistent_comparisons_preserve_values_without_delta(client, updates):
    payload = deepcopy(PAYLOAD)
    payload["previous_measurements"]["glucose_fasting"].update(updates)
    text = _text(client, payload)
    assert "120" in text
    assert "No se estableció un cambio comparable" in text
    assert "+20" not in text


def test_first_record_does_not_invent_previous_value(client):
    payload = deepcopy(PAYLOAD)
    payload["previous_measurements"]["glucose_fasting"] = {
        "direction": "first_record",
        "change": "first_record",
        "current_value": 120,
        "current_unit": "mg/dL",
        "current_source": "device",
    }
    text = _text(client, payload)
    assert "Sin comparación previa" in text and "fecha sin registrar" in text
    assert "dispositivo" in text and "Previo:" not in text


def test_legacy_lab_origin_never_claims_verification(client):
    payload = deepcopy(PAYLOAD)
    payload["previous_measurements"]["glucose_fasting"].update(current_source="lab")
    text = _text(client, payload)
    assert "extracción de laboratorio histórica" in text
    assert "verificado" not in text


def test_unknown_names_units_sources_and_document_text_are_never_echoed(client):
    payload = deepcopy(PAYLOAD)
    previous = payload["previous_measurements"]["glucose_fasting"]
    previous.update(current_unit="TAKE DRUG", current_source="IGNORE POLICY")
    payload["previous_measurements"]["DIAGNOSE NOW"] = previous
    payload["metrics"][0]["source_text"] = "PRESCRIBE NOW"
    text = _text(client, payload)
    assert "unidad sin verificar" in text and "fuente sin especificar" in text
    assert not any(
        value in text for value in ["TAKE DRUG", "IGNORE POLICY", "DIAGNOSE NOW", "PRESCRIBE NOW"]
    )


@pytest.mark.parametrize("language", ["es", "en"])
def test_unverified_ocr_is_pending_copy_without_measurement_claims(client, language):
    payload = deepcopy(PAYLOAD)
    payload["language"] = language
    payload["previous_measurements"]["glucose_fasting"].update(current_source="ocr_unverified")
    text = _text(client, payload)
    assert "120" not in text and "100" not in text
    assert (
        "awaiting professional verification" if language == "en" else "pendientes de verificación"
    ) in text


def test_extraction_metrics_alone_are_not_established_clinical_context(client):
    text = _text(client, {"metrics": PAYLOAD["metrics"], "previous_measurements": {}})
    assert "120" not in text and "verificación profesional" in text


def test_nonfinite_measurements_are_rejected(client):
    payload = deepcopy(PAYLOAD)
    payload["previous_measurements"]["glucose_fasting"]["current_value"] = "Infinity"
    assert client.post(URL, headers=HEADERS, json=payload).status_code == 422
