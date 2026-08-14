"""Runner de desarrollo para Windows: SelectorEventLoop (requisito psycopg async).

uvicorn 0.36+ en Windows fuerza ProactorEventLoop vía su loop_factory
(`uvicorn/loops/asyncio.py`), ignorando la política del proceso. La solución es
pasar `loop="asyncio:SelectorEventLoop"` a `uvicorn.run`: uvicorn lo importa
como fábrica de loop y crea un SelectorEventLoop (compatible con psycopg async).
"""
import sys

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        loop="asyncio:SelectorEventLoop" if sys.platform == "win32" else "auto",
    )
