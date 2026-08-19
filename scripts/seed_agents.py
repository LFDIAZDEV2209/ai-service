"""Seed de agentes en `ai.agent_runtime_configs` (Opción A).

Registra los perfiles de código (`app/agents/registry.py`) como configuración
activa en la BD, para que el front pueda editarlos y el routing los resuelva
desde la BD.

Uso:
    uv run python scripts/seed_agents.py
"""

from __future__ import annotations

import asyncio
import selectors
import sys
from pathlib import Path

# Permite importar `app` sin depender de PYTHONPATH: el script vive en
# scripts/, la raíz del proyecto es el directorio padre.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.registry import AGENTS  # noqa: E402
from app.db.engine import async_session  # noqa: E402
from app.db.models import AgentRuntimeConfig  # noqa: E402


def _run(coro) -> None:
    """Corre la corrutina con SelectorEventLoop (requerido por psycopg async
    en Windows; el Proactor por defecto falla)."""
    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()
    else:
        asyncio.run(coro)


def _build_config(profile) -> dict:
    """Traduce un `AgentProfile` al JSON `config` que espera AgentRuntimeConfig."""
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


async def _seed(re_sync: bool = False) -> None:
    async with async_session() as session:
        for key, profile in AGENTS.items():
            existing = (
                await session.execute(
                    AgentRuntimeConfig.__table__.select().where(
                        AgentRuntimeConfig.agent_type_id == key,
                        AgentRuntimeConfig.is_active.is_(True),
                    )
                )
            ).first()

            if existing is not None and not re_sync:
                print(f"[skip] {key}: ya tiene config activa en BD")
                continue

            if existing is not None:
                await session.execute(
                    AgentRuntimeConfig.__table__.update()
                    .where(AgentRuntimeConfig.agent_type_id == key)
                    .values(config=_build_config(profile), version_id="seed-1")
                )
                print(f"[re-sync] {key}: config restaurada desde código")
                continue

            session.add(
                AgentRuntimeConfig(
                    agent_type_id=key,
                    version_id="seed-1",
                    is_active=True,
                    config=_build_config(profile),
                )
            )
            print(f"[seed] {key}: configurado en BD")

        await session.commit()
        print("Seed completado.")


if __name__ == "__main__":
    _run(_seed(re_sync="--re-sync" in sys.argv))
