"""Contrato HTTP del SDK real con transporte falso; sin proveedor ni datos de pacientes."""

import importlib
import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from fastapi import FastAPI

from app.api.routes.content_translation import router
from app.llm import factory
from app.services import content_translation as service_module
from app.services.content_translation import (
    ContentTranslationService,
    get_content_translation_service,
)


@pytest.fixture
def provider_settings(monkeypatch):
    settings = SimpleNamespace(
        llm_provider="anthropic",
        llm_temperature=0.65,
        llm_max_tokens=4096,
        anthropic_api_key="dummy-test-key",
        anthropic_model="claude-sonnet-4-6",
        openai_api_key="dummy-test-key",
        openai_model="gpt-4o",
    )
    monkeypatch.setattr(factory, "get_settings", lambda: settings)
    return settings


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
@pytest.mark.parametrize("mode", ["configured", "explicit_zero", "explicit_none"])
def test_factory_distinguishes_inherited_sampling_zero_and_omission(
    provider_settings, provider, mode
):
    options = (
        {} if mode == "configured" else {"temperature": 0.0 if mode == "explicit_zero" else None}
    )
    model = factory.get_chat_model(provider=provider, **options)
    expected = {"configured": 0.65, "explicit_zero": 0.0, "explicit_none": None}[mode]
    assert model.temperature == expected
    assert (model.model if provider == "anthropic" else model.model_name) == (
        provider_settings.anthropic_model
        if provider == "anthropic"
        else provider_settings.openai_model
    )


def test_factory_explicit_omission_overrides_openai_o1_sdk_default(provider_settings):
    provider_settings.openai_model = "o1"
    model = factory.get_chat_model(provider="openai", temperature=None)
    assert model.model_name == "o1"
    assert model.temperature is None
    assert "temperature" not in model._default_params


def stream_tool_result(result, model: str, tool_name: str) -> str:
    """Respuesta SSE válida de Messages API para ejercitar también su parser real."""
    events = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_synthetic",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "tool_synthetic",
                    "name": tool_name,
                    "input": {},
                },
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": json.dumps(result)},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 1},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {name}\ndata: {json.dumps(event)}\n\n" for name, event in events)


@pytest.mark.parametrize("legacy_sampling", [False, True])
async def test_translation_sdk_wire_omits_rejected_sampling_and_validates_structured_output(
    provider_settings, monkeypatch, legacy_sampling, caplog
):
    # SDK 0.x usa httpx; SDK 2.x usa httpx2. Ningún transporte abre una conexión real.
    http = (
        httpx
        if issubclass(anthropic.DefaultAsyncHttpxClient, httpx.AsyncClient)
        else importlib.import_module("httpx2")
    )
    observed = []

    async def transport(request):
        body = json.loads(request.content)
        observed.append(body)
        # Error capturado por el fix productivo 73dbb59 de narrativa. El SDK moderno
        # reenvía temperature vía extra_body, aunque no figure en Messages.create.
        if "temperature" in body:
            return http.Response(
                400,
                json={
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "message": "temperature: Extra inputs are not permitted; PRIVATE",
                    },
                },
            )
        content = body["messages"][0]["content"]
        text = content if isinstance(content, str) else content[0]["text"]
        data = json.loads(text)
        result = {
            "target_language": "en",
            "items": [
                {
                    "id": item["id"],
                    "translation": item["text"]
                    .replace("Evaluación de hábitos", "Habit assessment")
                    .replace("Tomar", "Take"),
                }
                for item in data["items"]
            ],
        }
        return http.Response(
            200,
            content=stream_tool_result(result, body["model"], body["tools"][0]["name"]),
            headers={"content-type": "text/event-stream"},
        )

    client = anthropic.AsyncAnthropic(
        api_key="dummy-test-key",
        http_client=http.AsyncClient(transport=http.MockTransport(transport)),
        max_retries=0,
    )
    constructed = []
    real_factory = factory.get_chat_model

    def model_factory(**options):
        assert options == {"temperature": None, "max_tokens": 16000}
        if legacy_sampling:
            options["temperature"] = 0.0
        model = real_factory(**options)
        model.__dict__["_async_client"] = client
        constructed.append(model)
        return model

    monkeypatch.setattr(service_module, "get_chat_model", model_factory)
    service = ContentTranslationService()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_content_translation_service] = lambda: service
    from app.api.security import require_internal_key

    app.dependency_overrides[require_internal_key] = lambda: None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test"
        ) as api:
            response = await api.post(
                "/internal/content/translate",
                json={
                    "target_language": "en",
                    "items": [
                        {"id": "caller-assignment", "text": "Evaluación de hábitos"},
                        {"id": "caller-dose", "text": "Tomar **5 mg**"},
                    ],
                },
            )
    finally:
        await client.close()
    assert len(constructed) == len(observed) == 1
    body = observed[0]
    assert body["model"] == provider_settings.anthropic_model
    assert body["max_tokens"] == 16000
    assert body["stream"] is True
    assert body["tools"][0]["input_schema"]["additionalProperties"] is False
    assert "caller-assignment" not in json.dumps(body)
    assert "top_p" not in body and "top_k" not in body
    if legacy_sampling:
        assert body["temperature"] == 0.0
        assert response.status_code == 503
        assert response.json() == {"detail": "Translation unavailable"}
        assert not service._cache
        assert "PRIVATE" not in caplog.text
    else:
        assert "temperature" not in body
        assert response.status_code == 200
        assert response.json() == {
            "target_language": "en",
            "items": [
                {"id": "caller-assignment", "translation": "Habit assessment"},
                {"id": "caller-dose", "translation": "Take **5 mg**"},
            ],
        }
        assert len(service._cache) == 2
