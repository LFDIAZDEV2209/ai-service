# 06 — Desarrollo local

## Prerrequisitos

- `docker compose up -d` desde la raíz (PostgreSQL + Valkey).
- `ai-service/.env` con la sección ElevenLabs (ver 05; `.env.example` tiene
  los placeholders).
- Backend .NET con `appsettings.Development.json` válido (ya existente).

## Arranque

```bash
# Raíz: Postgres + Valkey
docker compose up -d

# AI Service (Windows: SIEMPRE run_dev.py; SelectorEventLoop para psycopg)
cd ai-service
uv sync
uv run python run_dev.py          # :8000

# Backend (API :5122 + Auth :5123 + Gateway :5080)
cd coppAddresdBack
.\scripts\dev-up.ps1              # levanta el stack; o dotnet run por proyecto

# App paciente
cd antares-paciente
npm run dev                       # :5173
```

## Flujo de sesión de voz en local

1. Login en la app (demo `paciente.prueba@mediquer.com` / `Demo1234!`,
   `application: "app"`).
2. Abrir el agente de voz (botón de voz en Inicio).
3. La app llama `POST /api/v1/chat/voice/session` (JWT) → Gateway → API →
   ai-service (`/internal/voice/session`) → ElevenLabs → `{signedUrl, agentId,
conversationId?}`.
4. La app conecta al WebSocket de ElevenLabs con el SDK.

Verificación por HTTP sin app:

```bash
# 1. login (token en body)
curl -X POST http://localhost:5080/api/auth/login -H "Content-Type: application/json" \
  -d '{"email":"paciente.prueba@mediquer.com","password":"Demo1234!","application":"app"}'
# 2. sesión de voz
curl -X POST http://localhost:5080/api/v1/chat/voice/session \
  -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" -d '{}'
```

200 = `{ "signedUrl": "wss://...", "agentId": "agent_...", "conversationId": ... }`.
La respuesta JAMÁS contiene la API key.

## Pruebas

```bash
# ai-service (unit + API de voz con MockTransport)
cd ai-service && uv run pytest -q && uv run ruff check .

# backend (1002 tests; la suite entera o el filtro de voz)
cd coppAddresdBack
dotnet test tests/CoppAddresd.UnitTests --filter "FullyQualifiedName~VoiceSession"
dotnet test tests/CoppAddresd.UnitTests

# app
cd antares-paciente
npm run lint && npm test -- --run && npm run build && npm run i18n:check
```

## Config de OpenCode para el agente de desarrollo

El MCP hosted de ElevenLabs (OAuth) está registrado global del usuario
(`02-mcp-setup.md`): permite crear/ajustar agentes por lenguaje natural.
En el repo NO se versiona la config del MCP (auth por usuario).
