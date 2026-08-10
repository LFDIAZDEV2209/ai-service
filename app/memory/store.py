"""Memoria de largo plazo (entre threads / usuarios).

Para producción se recomienda migrar al `Store` nativo de LangGraph
(postgres store) o una base de datos clave-valor. Aquí dejamos la interfaz
y una implementación JSON simple para desarrollo.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings


class MemoryStore(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> None: ...


class JsonFileMemoryStore:
    """Implementación simple sobre un archivo JSON (con lock para hilos)."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._data[key] = value
            self._persist()

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)
            self._persist()


def get_memory_store() -> MemoryStore:
    settings = get_settings()
    return JsonFileMemoryStore(settings.memory_store_path)
