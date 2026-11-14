"""Exporta OpenAPI local acotado a Vita. No conecta con BD ni proveedores."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://fixture:fixture@invalid/vita_test")
os.environ.setdefault("ENVIRONMENT", "test")

from app.api.main import create_app  # noqa: E402


def main() -> None:
    spec = create_app().openapi()
    spec["paths"] = {
        path: value for path, value in spec["paths"].items()
        if path.startswith(("/internal/voice/", "/api/v1/chat", "/api/v1/threads"))
        and "lab-exam" not in path
    }
    needed = set()

    def refs(value):
        if isinstance(value, dict):
            if "$ref" in value:
                needed.add(value["$ref"].rsplit("/", 1)[-1])
            for child in value.values():
                refs(child)
        elif isinstance(value, list):
            for child in value:
                refs(child)

    refs(spec["paths"])
    schemas = spec.get("components", {}).get("schemas", {})
    while True:
        previous = needed.copy()
        for name in previous:
            refs(schemas.get(name, {}))
        if previous == needed:
            break
    spec["components"]["schemas"] = {k: v for k, v in schemas.items() if k in needed}
    target = ROOT / "docs/contracts/vita-ai.openapi.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Contrato local: {target.relative_to(ROOT)}")
    for action, revision, filename in (
        ("upgrade", "e7041890ceaa:head", "vita-voice-migration.sql"),
        ("downgrade", "fe62a09c1741:e7041890ceaa", "vita-voice-rollback.sql"),
    ):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", action, revision, "--sql"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        sql = "\n".join(line.rstrip() for line in result.stdout.splitlines()).rstrip() + "\n"
        (target.parent / filename).write_text(sql, encoding="utf-8")
        print(f"SQL offline: docs/contracts/{filename}")


if __name__ == "__main__":
    main()
