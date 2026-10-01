# ElevenLabs en CoppAddresd — asistente de voz para pacientes

Integración profesional de **ElevenLabs Agents** (Conversational AI) para la
app de pacientes `antares-paciente`, siguiendo el plan de
`PROMPT_ELEVENLABS.md`. Estado: **implementado y verificado en desarrollo**
(flujo completo E2E real); despliegue a producción/TestFlight documentado.

## Índice

| Doc                          | Contenido                                                                                             |
| ---------------------------- | ----------------------------------------------------------------------------------------------------- |
| `01-current-architecture.md` | Auditoría previa (FASE 1): stack real, ruta del chat IA, MVP de voz Web Speech, puntos de integración |
| `02-mcp-setup.md`            | MCP hosted de ElevenLabs conectado a OpenCode (OAuth, región US), verificación y reconexión           |
| `03-target-architecture.md`  | Decisión A/B/C, responsabilidades, tools v1 (client tools), plan de fases                             |
| `04-twilio-integration.md`   | Fase futura: mismo agente en llamadas (webhook tools + identidad OTP/token)                           |
| `05-security-privacy.md`     | Modelo de seguridad, secretos, privacidad del agente (FASE 10 aplicada)                               |
| `06-local-development.md`    | Cómo correr y verificar el flujo en local                                                             |
| `07-deployment.md`           | Despliegue a producción/TestFlight (Secrets Manager + task def + workflows)                           |
| `08-testing.md`              | Suites automatizadas y prueba manual por device                                                       |
| `09-troubleshooting.md`      | Errores típicos y soluciones                                                                          |
| `agent-config.md`            | Config real del agente creado vía MCP (id, tools, prompt)                                             |
| `attachments.md`             | Attachments del chat: imágenes (soportado) y diseño de video/PDF/archivos                             |
| `ios-testflight.md`          | Checklist de release iOS/TestFlight (permisos, build, verificaciones)                                 |

## Arquitectura en una línea

```
App (JWT) → Gateway → API .NET POST /api/v1/chat/voice/session
          → ai-service /internal/voice/session (X-Internal-Key, API key aquí)
          → ElevenLabs signed URL → app ↔ ElevenLabs (SDK, sin key)
          → client tools ejecutadas por el device con el JWT del paciente
          → endpoints .NET existentes → PostgreSQL
```

ElevenLabs **nunca** toca la base de datos ni conoce la API key del lado
cliente. Cambiar de cuenta ElevenLabs = cambiar 3 variables de entorno.

## Estado de implementación

- ✅ MCP conectado a OpenCode (OAuth, región US) + verificación real.
- ✅ Agentes recreados vía MCP en la cuenta nueva (migración 2026-10-01):
  dev `agent_5501m3w6n1j7fe5tx02291nkvy8n` + prod `agent_1101m3w6pc64e9prz4bkvwct0hy9`
  (español, prompt clínico, 8 client tools de negocio, privacidad sanitaria).
- ✅ `ai-service`: `POST /internal/voice/session` + cliente ElevenLabs + tests.
- ✅ Backend .NET: `POST /api/v1/chat/voice/session` (JWT, validación,
  auditoría, errores amigables) + tests.
- ✅ App: `VoiceOverlay` con SDK oficial + client tools + fallback Web Speech,
  i18n, lint/build/tests verdes.
- ✅ E2E verificado en local (signed URL real devuelto al cliente).
- ✅ Rediseño UX (ChatGPT-like) de ChatPage + VoiceOverlay por agente de
  diseño en Herdr (Split Right), revisado e integrado por el principal.
- ⏳ Despliegue prod (patch único de Secrets Manager + task def, ver 07) y
  prueba en TestFlight.
