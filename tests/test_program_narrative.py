"""Pruebas del contrato y la redacción, sin proveedor LLM ni base de datos."""

import copy
import json
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.main import create_app
from app.api.routes.program_narrative import get_narrative_model
from app.core.config import get_settings
from app.schemas.program_narrative import ProgramNarrativeRequest, ProgramNarratives
from app.services.program_narrative import generate_program_narrative

URL = "/internal/program/assignment-narrative"
HEADERS = {"X-Internal-Key": get_settings().internal_api_key}
SOURCE = "criterio-demo §2"
PAYLOAD = {
    "profile_snapshot": {
        "results": [{"score": 12, "subscale": "movimiento", "indicator": "bajo"}],
        "biometrics": {"weight": 80, "glucose": None},
    },
    "proposal": {
        "nutritionPlan": {"id": "plan-demo"},
        "routine": None,
        "matchedRules": [{"code": "regla-demo", "source_ref": SOURCE}],
    },
    "language": "es",
    "source_refs": [SOURCE],
}
OUTPUT = {"es": f"Justificación de la propuesta [{SOURCE}].", "en": f"Rationale [{SOURCE}]."}


@pytest.fixture
def narrative_client():
    """Sustituye únicamente el modelo para ejecutar el endpoint completo."""
    model = Mock()
    runnable = Mock()
    runnable.ainvoke = AsyncMock(return_value=OUTPUT)
    model.with_structured_output.return_value = runnable
    app = create_app()
    app.dependency_overrides[get_narrative_model] = lambda: model
    with TestClient(app) as client:
        yield client, runnable
    app.dependency_overrides.clear()


@pytest.mark.parametrize("headers", [{}, {"X-Internal-Key": "incorrecta"}])
def test_authentication(narrative_client, headers):
    client, runnable = narrative_client
    assert client.post(URL, json=PAYLOAD, headers=headers).status_code == 401
    runnable.ainvoke.assert_not_called()


@pytest.mark.parametrize("language", ["es", "en"])
def test_bilingual_response_preserves_snapshot_and_proposal(narrative_client, language):
    client, runnable = narrative_client
    payload = copy.deepcopy(PAYLOAD)
    payload["language"] = language
    response = client.post(URL, json=payload, headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"narratives": OUTPUT}
    messages = runnable.ainvoke.call_args.args[0]
    assert json.loads(messages[1].content) == payload
    assert "NUNCA" in messages[0].content
    assert "NEVER" in messages[0].content
    assert "[corchetes]" in messages[0].content


@pytest.mark.parametrize("field", ["profile_snapshot", "proposal", "language", "source_refs"])
def test_top_level_null_rejected(narrative_client, field):
    client, runnable = narrative_client
    payload = {**PAYLOAD, field: None}
    assert client.post(URL, json=payload, headers=HEADERS).status_code == 422
    runnable.ainvoke.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        {"language": "fr"},
        {"source_refs": [None]},
        {"source_refs": [""]},
        {"source_refs": ["x" * 501]},
        {"source_refs": ["x"] * 101},
        {"profile_snapshot": {"value": "x" * 100_000}},
        {"proposal": {"value": "x" * 100_000}},
    ],
)
def test_request_limits(narrative_client, changes):
    client, runnable = narrative_client
    assert client.post(URL, json={**PAYLOAD, **changes}, headers=HEADERS).status_code == 422
    runnable.ainvoke.assert_not_called()


def test_request_boundary_and_nested_nulls():
    payload = ProgramNarrativeRequest.model_validate(
        {**PAYLOAD, "source_refs": ["x" * 500] * 100}
    )
    assert payload.profile_snapshot["biometrics"]["glucose"] is None
    assert payload.proposal["routine"] is None
    assert ProgramNarrativeRequest.model_validate(
        {**PAYLOAD, "profile_snapshot": {"x": "a" * 99_991}}
    )


@pytest.mark.parametrize("language", ["es", "en"])
@pytest.mark.parametrize("value", [None, "", "   ", "x" * 2001])
def test_narrative_null_and_length_limits(language, value):
    with pytest.raises(ValidationError):
        ProgramNarratives.model_validate({**OUTPUT, language: value})


def test_narrative_maximum_length():
    assert len(ProgramNarratives(es="x" * 2000, en="y" * 2000).es) == 2000


@pytest.mark.parametrize(
    "output",
    [
        {"es": "[inventada]", "en": f"[{SOURCE}]"},
        {**OUTPUT, "en": None},
        {**OUTPUT, "proposal": {"nutritionPlan": "otro"}},
    ],
)
def test_invalid_model_output_is_unavailable(narrative_client, output):
    client, runnable = narrative_client
    runnable.ainvoke.return_value = output
    response = client.post(URL, json=PAYLOAD, headers=HEADERS)
    assert response.status_code == 503
    assert response.json() == {"detail": "Narrativa no disponible"}


def test_provider_failure_does_not_return_fabricated_text(narrative_client):
    client, runnable = narrative_client
    runnable.ainvoke.side_effect = RuntimeError("error privado del proveedor")
    response = client.post(URL, json=PAYLOAD, headers=HEADERS)
    assert response.status_code == 503
    assert "privado" not in response.text
    assert runnable.ainvoke.await_count == 1


@pytest.mark.parametrize(
    "output",
    [OUTPUT, {"es": "Sin cita", "en": "No citation"}],
)
async def test_service_accepts_subset_without_retry(output):
    model = Mock()
    runnable = model.with_structured_output.return_value
    runnable.ainvoke = AsyncMock(return_value=output)
    payload = ProgramNarrativeRequest.model_validate(
        {**PAYLOAD, "source_refs": [SOURCE, "otra-fuente"]}
    )
    result = await generate_program_narrative(payload, model)
    assert result.model_dump() == output
    assert runnable.ainvoke.await_count == 1


@pytest.mark.parametrize("invalid_language", ["es", "en"])
@pytest.mark.parametrize("success_attempt", [2, 3])
async def test_service_retries_foreign_citations_with_same_conversation(
    invalid_language, success_attempt
):
    model = Mock()
    runnable = model.with_structured_output.return_value
    invalid = {**OUTPUT, invalid_language: "Fuente ajena [si]"}
    runnable.ainvoke = AsyncMock(side_effect=[invalid] * (success_attempt - 1) + [OUTPUT])
    payload = ProgramNarrativeRequest.model_validate(PAYLOAD)
    result = await generate_program_narrative(payload, model)
    assert result.model_dump() == OUTPUT
    assert runnable.ainvoke.await_count == success_attempt
    calls = runnable.ainvoke.await_args_list
    assert all(call.args[0] is calls[0].args[0] for call in calls)
    model.with_structured_output.assert_called_once()
    schema = model.with_structured_output.call_args.args[0]
    assert "maxLength" not in schema["properties"]["es"]


@pytest.mark.parametrize("sources", [[], [SOURCE]])
async def test_service_exhausts_three_attempts_for_foreign_citations(sources):
    model = Mock()
    runnable = model.with_structured_output.return_value
    runnable.ainvoke = AsyncMock(return_value={**OUTPUT, "en": "Foreign source [si]"})
    payload = ProgramNarrativeRequest.model_validate({**PAYLOAD, "source_refs": sources})
    with pytest.raises(ValueError, match="La narrativa debe citar únicamente todas"):
        await generate_program_narrative(payload, model)
    assert runnable.ainvoke.await_count == 3


def test_without_sources(narrative_client):
    client, runnable = narrative_client
    runnable.ainvoke.return_value = {"es": "Propuesta recibida.", "en": "Proposal received."}
    assert client.post(URL, json={**PAYLOAD, "source_refs": []}, headers=HEADERS).status_code == 200


@pytest.mark.parametrize("success_attempt", [2, 3])
async def test_long_narratives_retry_and_recover(success_attempt):
    model = Mock()
    runnable = model.with_structured_output.return_value
    long_output = {"es": "x" * 2100, "en": "y" * 2200}
    runnable.ainvoke = AsyncMock(side_effect=[long_output] * (success_attempt - 1) + [OUTPUT])
    result = await generate_program_narrative(
        ProgramNarrativeRequest.model_validate(PAYLOAD), model
    )
    assert result.model_dump() == OUTPUT
    assert runnable.ainvoke.await_count == success_attempt


def test_long_narratives_clamped_after_three_attempts(narrative_client):
    client, runnable = narrative_client
    runnable.ainvoke.return_value = {"es": "x" * 2100, "en": "y" * 2200}
    response = client.post(URL, json=PAYLOAD, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["narratives"] == {"es": "x" * 1998 + "…", "en": "y" * 1998 + "…"}
    assert runnable.ainvoke.await_count == 3


def test_clamp_preserves_complete_citations(narrative_client):
    client, runnable = narrative_client
    runnable.ainvoke.return_value = {**OUTPUT, "es": "x" * 1995 + f"[{SOURCE}]" + "y" * 100}
    response = client.post(URL, json=PAYLOAD, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["narratives"]["es"] == "x" * 1995 + "…"
    assert response.json()["narratives"]["en"] == OUTPUT["en"]


def test_long_output_cannot_hide_invented_citations_by_truncation(narrative_client):
    client, runnable = narrative_client
    runnable.ainvoke.return_value = {**OUTPUT, "es": "x" * 2100 + "[si]"}
    assert client.post(URL, json=PAYLOAD, headers=HEADERS).status_code == 503
    assert runnable.ainvoke.await_count == 3


@pytest.mark.parametrize("success_attempt", [2, 3])
async def test_validation_error_from_model_retries_and_recovers(success_attempt):
    with pytest.raises(ValidationError) as error:
        ProgramNarratives(es="x" * 2100, en="y" * 2100)
    model = Mock()
    runnable = model.with_structured_output.return_value
    runnable.ainvoke = AsyncMock(side_effect=[error.value] * (success_attempt - 1) + [OUTPUT])
    result = await generate_program_narrative(
        ProgramNarrativeRequest.model_validate(PAYLOAD), model
    )
    assert result.model_dump() == OUTPUT
    assert runnable.ainvoke.await_count == success_attempt
