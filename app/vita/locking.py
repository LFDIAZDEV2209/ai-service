"""Exclusión por hilo entre réplicas y canales, con lock transaccional PostgreSQL."""

import hashlib
from contextlib import aclosing, asynccontextmanager

from sqlalchemy import text

from app.db.engine import engine


class ThreadBusyError(Exception):
    pass


@asynccontextmanager
async def thread_guard(storage_thread_id: str):
    key = int.from_bytes(
        hashlib.sha256(storage_thread_id.encode()).digest()[:8], "big", signed=True
    )
    async with engine.connect() as connection, connection.begin():
        acquired = await connection.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": key},
        )
        if not acquired:
            raise ThreadBusyError("vita_thread_busy")
        yield


class LockedGraph:
    def __init__(self, graph, reason: str):
        self.graph = graph
        self.reason = reason

    def __getattr__(self, name):
        return getattr(self.graph, name)

    async def ainvoke(self, *args, config, **kwargs):
        async with thread_guard(config["configurable"]["thread_id"]):
            return await self.graph.ainvoke(*args, config=config, **kwargs)

    async def astream(self, *args, config, **kwargs):
        stream = self.graph.astream(*args, config=config, **kwargs)
        async with thread_guard(config["configurable"]["thread_id"]), aclosing(stream):
            async for item in stream:
                yield item
