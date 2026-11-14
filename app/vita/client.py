"""Única frontera HTTP con la fachada autorizada del backend; nunca lee planes en SQL."""

import asyncio
import json
from typing import Any

import httpx
from pydantic import ValidationError

from app.core.config import get_settings
from app.vita.contracts import ContextEnvelope, Scope

MAX_RESPONSE_BYTES = 64_000


class VitaAccessError(Exception):
    """Fallo cerrado del catálogo/identidad, sin incluir payload ni credenciales."""


async def _get(path: str, user_id: str, **params: str) -> tuple[int, Any]:
    settings = get_settings()
    if not user_id or not settings.backend_base_url or not settings.internal_api_key:
        raise VitaAccessError("vita_unavailable")
    async with (
        asyncio.timeout(settings.vita_http_timeout),
        httpx.AsyncClient(
            timeout=settings.vita_http_timeout,
            follow_redirects=False,
        ) as client,
        client.stream(
            "GET",
            f"{settings.backend_base_url.rstrip('/')}/api/v1/internal/vita/{path}",
            params={"userId": user_id, **params},
            headers={"X-Internal-Key": settings.internal_api_key},
        ) as response,
    ):
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise VitaAccessError("vita_response_limit")
        return response.status_code, json.loads(body) if body else None


async def read_context(scope: Scope, user_id: str) -> ContextEnvelope:
    """La identidad la aporta el runtime, nunca el esquema de argumentos del LLM."""
    try:
        code, body = await _get("context", user_id, scope=scope)
        if code == 401:
            return ContextEnvelope(status="forbidden")
        statuses = {200: {"ok", "empty"}, 403: {"forbidden"}, 409: {"stale"}, 503: {"unavailable"}}
        if code not in statuses:
            # 404 de una fachada ausente no acredita que el paciente no tenga plan.
            return ContextEnvelope(status="unavailable")
        envelope = ContextEnvelope.model_validate(body)
        if envelope.status not in statuses[code]:
            return ContextEnvelope(status="unavailable")
        if envelope.status != "ok":
            envelope.actions = []
        if envelope.status not in {"ok", "empty"}:
            envelope.data = None
            envelope.sources = []
        return envelope
    except (httpx.HTTPError, TimeoutError, ValueError, ValidationError, VitaAccessError):
        return ContextEnvelope(status="unavailable")


async def read_catalog(user_id: str) -> Any:
    try:
        code, body = await _get("agents", user_id)
        if code != 200:
            raise VitaAccessError("vita_forbidden" if code in (401, 403) else "vita_unavailable")
        return body
    except (httpx.HTTPError, TimeoutError, ValueError) as exc:
        raise VitaAccessError("vita_unavailable") from exc
