"""Punto de entrada del servicio.

Uso:
    uvicorn main:app --reload
    python main.py
"""

import uvicorn

from app.api.main import create_app
from app.core.config import get_settings

app = create_app()

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.environment == "development",
    )
