FROM python:3.13-slim

WORKDIR /app

# Dependencias de producción primero (aprovecha el cache de capas de Docker)
COPY requirements-prod.txt .
RUN pip install --no-cache-dir -r requirements-prod.txt

# Código fuente
COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
