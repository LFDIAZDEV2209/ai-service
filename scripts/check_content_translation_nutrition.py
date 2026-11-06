"""Prueba voluntaria del proveedor configurado con nutrición exclusivamente sintética."""

import argparse
import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path

import httpx
from fastapi import FastAPI

from app.api.routes.content_translation import router
from app.core.config import get_settings
from app.services.content_translation import (
    ContentTranslationService,
    _PROTECTED,
    get_content_translation_service,
)


async def check(items: int, output: Path | None) -> bool:
    """Mide una llamada fría y otra con cache, sin imprimir secretos ni textos."""
    fixture = (
        Path(__file__).resolve().parents[1] / "tests/fixtures/content_translation_nutrition.json"
    )
    sources = json.loads(fixture.read_text())["sources"][:items]
    payload = {
        "target_language": "en",
        "items": [
            {"id": f"synthetic-nutrition-{index}", "text": source}
            for index, source in enumerate(sources)
        ],
    }
    settings = get_settings()
    service = ContentTranslationService()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_content_translation_service] = lambda: service
    report = {
        "synthetic_only": True,
        "provider": settings.llm_provider,
        "model": settings.anthropic_model
        if settings.llm_provider == "anthropic"
        else settings.openai_model,
        "item_count": len(sources),
        "unique_source_count": len(set(sources)),
        "source_characters": sum(map(len, sources)),
        "protected_span_count": sum(len(_PROTECTED.findall(source)) for source in sources),
        "dataset_sha256": hashlib.sha256(
            json.dumps(payload, ensure_ascii=False).encode()
        ).hexdigest(),
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test", timeout=45
    ) as client:
        started = time.monotonic()
        response = await client.post(
            "/internal/content/translate",
            json=payload,
            headers={"X-Internal-Key": settings.internal_api_key},
        )
        report["status"] = response.status_code
        report["seconds"] = round(time.monotonic() - started, 3)
        report["cached_items_after_first"] = len(service._cache)
        report["response"] = response.json()
        passed = response.status_code == 200
        if passed:
            translated = response.json()["items"]
            report["exact_ids_and_order"] = [item["id"] for item in translated] == [
                item["id"] for item in payload["items"]
            ]
            report["all_protected_spans_preserved"] = len(translated) == len(sources) and all(
                _PROTECTED.findall(source) == _PROTECTED.findall(target["translation"])
                for source, target in zip(sources, translated, strict=True)
            )
            started = time.monotonic()
            repeat = await client.post(
                "/internal/content/translate",
                json=payload,
                headers={"X-Internal-Key": settings.internal_api_key},
            )
            report["repeat_status"] = repeat.status_code
            report["repeat_seconds"] = round(time.monotonic() - started, 3)
            report["identical_repeat"] = repeat.json() == response.json()
            passed = all(
                (
                    report["exact_ids_and_order"],
                    report["all_protected_spans_preserved"],
                    report["identical_repeat"],
                    report["cached_items_after_first"] == len(set(sources)),
                    report["seconds"] < 30,
                    repeat.status_code == 200,
                )
            )
        if output is not None:
            output.write_text(
                json.dumps({**report, "sources": sources}, ensure_ascii=False, indent=2) + "\n"
            )
        print(json.dumps({key: value for key, value in report.items() if key != "response"}))
        return passed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real-provider",
        action="store_true",
        help="Autoriza llamadas reales al modelo configurado",
    )
    parser.add_argument("--items", choices=(56, 64), type=int, default=64)
    parser.add_argument("--output", type=Path, help="Guarda evidencia exclusivamente sintética")
    args = parser.parse_args()
    if not args.real_provider:
        parser.error("La prueba exige --real-provider y usa la configuración existente.")
    # El script solo imprime métricas; las trazas de librerías no deben revelar la configuración.
    logging.disable(logging.CRITICAL)
    try:
        passed = asyncio.run(check(args.items, args.output))
    except Exception as exc:
        print(json.dumps({"synthetic_only": True, "exception_type": type(exc).__name__}))
        raise SystemExit(1) from None
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
