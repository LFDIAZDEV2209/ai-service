"""Seed de agentes en el ERP + sincronización al AI Service.

Crea los 3 agentes especializados (nutrición, medicina, psicología) en el
schema `agents` del ERP, les asigna una versión activa, y sincroniza la config
al AI Service vía `/internal/agents/sync-config`. El seed es idempotente:
si el agente ya existe, lo actualiza; si la versión ya existe, la sobreescribe.

Uso:
    cd ai-service
    uv run python scripts/seed_erp_agents.py
"""

from __future__ import annotations

import json
import selectors
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio
import httpx
from sqlalchemy import text

from app.agents.registry import AGENTS
from app.core.config import get_settings
from app.db.engine import async_session

# ── Config de sincronización ────────────────────────────────────────────────

INTERNAL_KEY = "gJqyFxjN47EB6w5i3H2U0PMLWVCDGshampS8zTcA"  # del .env
AI_SERVICE_URL = "http://localhost:8000"

AGENTS_ERP = [
    {
        "slug": "nutrition",
        "name": "Especialista en Nutrición",
        "description": "Responde consultas sobre alimentación, dietas y nutrición para pacientes con obesidad.",
        "specialty": "Nutrición",
        "icon_key": "Salad",
    },
    {
        "slug": "medical",
        "name": "Especialista en Salud",
        "description": "Responde consultas generales de salud, síntomas y orientación clínica.",
        "specialty": "Medicina General",
        "icon_key": "Stethoscope",
    },
    {
        "slug": "psychology",
        "name": "Especialista en Salud Mental",
        "description": "Responde consultas de bienestar emocional, ansiedad y salud mental.",
        "specialty": "Psicología",
        "icon_key": "BrainCircuit",
    },
]


def _build_config(profile) -> dict:
    return {
        "system_prompt": profile.system_prompt,
        "provider": profile.provider,
        "model": profile.model,
        "temperature": None,
        "max_tokens": None,
        "tools": list(profile.tools),
        "retrieval_config": {"enabled": False, "knowledge_base_ids": [], "top_k": 5},
        "memory_config": {"enabled": False, "categories": []},
    }


def _run(coro) -> None:
    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()
    else:
        asyncio.run(coro)


async def _seed_and_sync() -> None:
    now = datetime.now(UTC)

    async with async_session() as session:
        # Obtener la tabla de agent_types y agent_type_versions vía SQL raw
        for agent_def in AGENTS_ERP:
            slug = agent_def["slug"]
            profile = AGENTS.get(slug)
            if profile is None:
                print(f"[skip] {slug}: no existe profile en registry")
                continue

            config_json = json.dumps(_build_config(profile), ensure_ascii=False)

            # Buscar si ya existe el agente por slug
            existing = await session.execute(
                text(
                    'SELECT id FROM agents.agent_types WHERE slug = :slug'
                ),
                {"slug": slug},
            )
            row = existing.first()

            if row is not None:
                agent_id = row[0]
                print(f"[update] {slug}: agente existente (id={agent_id})")
            else:
                agent_id = uuid.uuid4()
                await session.execute(
                    text(
                        """INSERT INTO agents.agent_types
                        (id, name, description, specialty, icon_key, slug, status, metadata, created_at)
                        VALUES (:id, :name, :desc, :spec, :icon, :slug, 'Activo', '{}', :now)"""
                    ),
                    {
                        "id": agent_id,
                        "name": agent_def["name"],
                        "desc": agent_def["description"],
                        "spec": agent_def["specialty"],
                        "icon": agent_def["icon_key"],
                        "slug": slug,
                        "now": now,
                    },
                )
                print(f"[create] {slug}: agente nuevo (id={agent_id})")

            # Crear versión (idempotente: buscar versión existente)
            ver_existing = await session.execute(
                text(
                    'SELECT id FROM agents.agent_type_versions WHERE agent_type_id = :aid AND version_number = 1'
                ),
                {"aid": agent_id},
            )
            ver_row = ver_existing.first()

            if ver_row is not None:
                version_id = ver_row[0]
                await session.execute(
                    text(
                        "UPDATE agents.agent_type_versions SET config = :cfg WHERE id = :vid"
                    ),
                    {"cfg": config_json, "vid": version_id},
                )
                print(f"  [update] versión 1 existente")
            else:
                version_id = uuid.uuid4()
                await session.execute(
                    text(
                        """INSERT INTO agents.agent_type_versions
                        (id, agent_type_id, version_number, is_active, config, created_at)
                        VALUES (:vid, :aid, 1, true, :cfg, :now)"""
                    ),
                    {"vid": version_id, "aid": agent_id, "cfg": config_json, "now": now},
                )
                print(f"  [create] versión 1")

            # Asignar versión activa
            await session.execute(
                text(
                    "UPDATE agents.agent_types SET active_version_id = :vid WHERE id = :aid"
                ),
                {"vid": version_id, "aid": agent_id},
            )

        await session.commit()
        print("\nAgentes del ERP creados/actualizados.")

    # ── Sync al AI Service ──────────────────────────────────────────────
    print("\nSincronizando al AI Service...")
    async with httpx.AsyncClient(base_url=AI_SERVICE_URL, timeout=30) as client:
        for agent_def in AGENTS_ERP:
            slug = agent_def["slug"]
            profile = AGENTS.get(slug)
            if profile is None:
                continue

            payload = {
                "agent_type_id": slug,
                "version_id": f"erp-{slug}-v1",
                "version_number": 1,
                "name": agent_def["name"],
                "description": agent_def["description"],
                "specialty": agent_def["specialty"],
                "icon_key": agent_def["icon_key"],
                "config": _build_config(profile),
            }

            try:
                resp = await client.post(
                    "/internal/agents/sync-config",
                    json=payload,
                    headers={"X-Internal-Key": INTERNAL_KEY},
                )
                resp.raise_for_status()
                print(f"  [sync] {slug}: OK")
            except httpx.HTTPStatusError as exc:
                print(f"  [sync] {slug}: HTTP {exc.response.status_code}")
            except httpx.ConnectError:
                print(f"  [sync] {slug}: AI Service no disponible (sin sync)")

    print("\nSeed completado.")


if __name__ == "__main__":
    _run(_seed_and_sync())
