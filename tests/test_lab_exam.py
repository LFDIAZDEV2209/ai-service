"""Tests del endpoint de extracción de exámenes de laboratorio (POST /chat/lab-exam)."""

import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.api.deps import get_db_session
from app.api.main import create_app
from app.api.routes.lab_exam import get_lab_exam_model
from app.core.config import get_settings

INTERNAL_KEY = get_settings().internal_api_key
HEADERS = {"X-Internal-Key": INTERNAL_KEY}


class FakeDbSession:
    """Sesión de BD fake para tests."""

    async def add(self, *args, **kwargs):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def get(self, *args, **kwargs):
        return None

    async def execute(self, *args, **kwargs):
        return None


class FakeModel:
    """Modelo LLM fake que devuelve la respuesta preconfigurada."""

    def __init__(self, response_text: str):
        self.response_text = response_text
        self.invoked_with = []

    async def ainvoke(self, messages, *args, **kwargs):
        self.invoked_with.append(messages)
        return AIMessage(content=self.response_text)


@pytest.fixture
def fake_model_success():
    llm_json = {
        "readable": True,
        "summary": "Se detectaron 2 métricas de laboratorio: glucosa en ayunas y HbA1c.",
        "metrics": [
            {
                "metric_name": "glucose_fasting",
                "value": 98.5,
                "unit_symbol": "mg/dL",
                "observed_at": "2026-09-01T08:00:00Z",
            },
            {
                "metric_name": "hba1c",
                "value": 5.4,
                "unit_symbol": "%",
                "observed_at": "2026-09-01T08:00:00Z",
            },
        ],
    }
    return FakeModel(json.dumps(llm_json))


@pytest.fixture
def fake_model_empty():
    llm_json = {
        "readable": True,
        "summary": "No lab values from the supported catalog were detected in this document.",
        "metrics": [],
    }
    return FakeModel(json.dumps(llm_json))


@pytest.fixture
def fake_model_unreadable():
    llm_json = {
        "readable": False,
        "summary": "The document appears unreadable. Please upload a clearer scan.",
        "metrics": [],
    }
    return FakeModel(json.dumps(llm_json))


@pytest.fixture
def test_app():
    app = create_app()
    app.dependency_overrides[get_db_session] = FakeDbSession
    return app


def test_unauthorized_without_internal_key(test_app):
    """Rechaza requests sin el header X-Internal-Key con 401."""
    with TestClient(test_app) as client:
        res = client.post(
            "/chat/lab-exam",
            data={"patient_id": "p-123", "batch_id": "b-456"},
            files={"file": ("exam.jpg", b"fake-jpeg-content", "image/jpeg")},
        )
        assert res.status_code == 401


def test_successful_extraction(test_app, fake_model_success):
    """Extrae exitosamente métricas del catálogo invocando el modelo de visión."""
    test_app.dependency_overrides[get_lab_exam_model] = lambda: fake_model_success

    with TestClient(test_app) as client:
        res = client.post(
            "/chat/lab-exam",
            headers=HEADERS,
            data={"patient_id": "p-123", "batch_id": "b-456", "thread_id": "t-789"},
            files={"file": ("exam.jpg", b"fake-jpeg-content", "image/jpeg")},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["readable"] is True
        assert len(body["metrics"]) == 2
        assert body["metrics"][0]["metric_name"] == "glucose_fasting"
        assert body["metrics"][0]["value"] == 98.5
        assert body["metrics"][0]["unit_symbol"] == "mg/dL"
        assert body["metrics"][1]["metric_name"] == "hba1c"
        assert body["metrics"][1]["value"] == 5.4
        assert "glucosa" in body["summary"].lower()


def test_unreadable_or_corrupt_document_flagged(test_app, fake_model_unreadable):
    """Documento ilegible o corrupto retorna readable=False y metrics=[]."""
    test_app.dependency_overrides[get_lab_exam_model] = lambda: fake_model_unreadable

    with TestClient(test_app) as client:
        res = client.post(
            "/chat/lab-exam",
            headers=HEADERS,
            data={"patient_id": "p-123", "batch_id": "b-456"},
            files={"file": ("blurry.jpg", b"blurry-bytes", "image/jpeg")},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["readable"] is False
        assert body["metrics"] == []
        assert "unreadable" in body["summary"].lower() or "ilegible" in body["summary"].lower()


def test_empty_catalog_metrics_in_readable_document(test_app, fake_model_empty):
    """Documento legible sin métricas del catálogo retorna readable=True y metrics=[]."""
    test_app.dependency_overrides[get_lab_exam_model] = lambda: fake_model_empty

    with TestClient(test_app) as client:
        res = client.post(
            "/chat/lab-exam",
            headers=HEADERS,
            data={"patient_id": "p-123", "batch_id": "b-456"},
            files={"file": ("narrative.jpg", b"narrative-bytes", "image/jpeg")},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["readable"] is True
        assert body["metrics"] == []
        assert "no lab values" in body["summary"].lower()


def test_empty_file_returns_unreadable(test_app):
    """Archivo vacío (0 bytes) retorna inmediatamente readable=False sin llamar al LLM."""
    with TestClient(test_app) as client:
        res = client.post(
            "/chat/lab-exam",
            headers=HEADERS,
            data={"patient_id": "p-123", "batch_id": "b-456"},
            files={"file": ("empty.jpg", b"", "image/jpeg")},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["readable"] is False
        assert body["metrics"] == []


def test_oversized_file_rejected(test_app):
    """Archivos mayores a 10 MB son rechazados con HTTP 400."""
    eleven_mb = b"0" * (11 * 1024 * 1024)
    with TestClient(test_app) as client:
        res = client.post(
            "/chat/lab-exam",
            headers=HEADERS,
            data={"patient_id": "p-123", "batch_id": "b-456"},
            files={"file": ("huge.jpg", eleven_mb, "image/jpeg")},
        )
        assert res.status_code == 400


def test_endpoint_accessible_via_api_v1_prefix(test_app, fake_model_success):
    """Verifica que el endpoint también es alcanzable bajo /api/v1/chat/lab-exam."""
    test_app.dependency_overrides[get_lab_exam_model] = lambda: fake_model_success

    with TestClient(test_app) as client:
        res = client.post(
            "/api/v1/chat/lab-exam",
            headers=HEADERS,
            data={"patient_id": "p-123", "batch_id": "b-456"},
            files={"file": ("exam.jpg", b"fake-jpeg-content", "image/jpeg")},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["readable"] is True
        assert len(body["metrics"]) == 2


def test_extraction_response_includes_empty_empathetic_message(test_app, fake_model_success):
    """La respuesta de extracción incluye `empathetic_message` vacío (campo aditivo)."""
    test_app.dependency_overrides[get_lab_exam_model] = lambda: fake_model_success

    with TestClient(test_app) as client:
        res = client.post(
            "/chat/lab-exam",
            headers=HEADERS,
            data={"patient_id": "p-123", "batch_id": "b-456"},
            files={"file": ("exam.jpg", b"fake-jpeg-content", "image/jpeg")},
        )
        assert res.status_code == 200
        assert res.json()["empathetic_message"] == ""


def test_missing_required_form_fields(test_app):
    """Falta de patient_id o batch_id retorna HTTP 422."""
    with TestClient(test_app) as client:
        res = client.post(
            "/chat/lab-exam",
            headers=HEADERS,
            data={"patient_id": "p-123"},  # falta batch_id
            files={"file": ("exam.jpg", b"content", "image/jpeg")},
        )
        assert res.status_code == 422


def test_filters_out_non_catalog_metrics(test_app):
    """Ignora métricas fuera de las 14 soportadas por el catálogo."""
    llm_json = {
        "readable": True,
        "summary": "Reporte de laboratorio variado",
        "metrics": [
            {"metric_name": "glucose_fasting", "value": 95.0, "unit_symbol": "mg/dL"},
            {"metric_name": "cholesterol_total", "value": 210.0, "unit_symbol": "mg/dL"},
            {"metric_name": "white_blood_cells", "value": 6500.0, "unit_symbol": "/uL"},
            {"metric_name": "systolic_bp", "value": 120.0, "unit_symbol": "mmHg"},
        ],
    }
    test_app.dependency_overrides[get_lab_exam_model] = lambda: FakeModel(json.dumps(llm_json))

    with TestClient(test_app) as client:
        res = client.post(
            "/chat/lab-exam",
            headers=HEADERS,
            data={"patient_id": "p-123", "batch_id": "b-456"},
            files={"file": ("exam.jpg", b"content", "image/jpeg")},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["readable"] is True
        # Solo glucose_fasting y systolic_bp deben preservarse
        assert len(body["metrics"]) == 2
        names = {m["metric_name"] for m in body["metrics"]}
        assert names == {"glucose_fasting", "systolic_bp"}


def test_corrupt_pdf_file_returns_unreadable(test_app):
    """Un PDF corrupto retorna readable=False sin fallar con 500."""
    with TestClient(test_app) as client:
        res = client.post(
            "/chat/lab-exam",
            headers=HEADERS,
            data={"patient_id": "p-123", "batch_id": "b-456"},
            files={"file": ("exam.pdf", b"not-a-real-pdf-content", "application/pdf")},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["readable"] is False
        assert body["metrics"] == []


def test_llm_exception_returns_unreadable(test_app):
    """Si el LLM arroja un error inesperado, se responde con readable=False."""

    class FailingModel:
        async def ainvoke(self, *args, **kwargs):
            raise RuntimeError("LLM service unavailable")

    test_app.dependency_overrides[get_lab_exam_model] = lambda: FailingModel()

    with TestClient(test_app) as client:
        res = client.post(
            "/chat/lab-exam",
            headers=HEADERS,
            data={"patient_id": "p-123", "batch_id": "b-456"},
            files={"file": ("exam.jpg", b"image-content", "image/jpeg")},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["readable"] is False
        assert body["metrics"] == []
