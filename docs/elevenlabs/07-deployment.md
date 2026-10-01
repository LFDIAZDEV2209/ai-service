# 07 — Despliegue (TestFlight / producción)

> FASE 13/14 del plan. La app TestFlight apunta a `https://erp.coppadresd.com`
> (`antares-paciente/.env.production`): el endpoint nuevo debe existir allí.

## Cambios que se despliegan

| Servicio              | Cambio                                                                   | Workflow                                             |
| --------------------- | ------------------------------------------------------------------------ | ---------------------------------------------------- |
| `ai-service`          | módulo `app/voice/` + `POST /internal/voice/session` + config ElevenLabs | `deploy-ai.yml` (push a `dev` del repo ai-service)   |
| `coppAddresdBack` API | `POST /api/v1/chat/voice/session` (+ DTOs, MediatR, cliente)             | `deploy-backend.yml` (push a `dev` del repo backend) |
| `antares-paciente`    | VoiceOverlay ElevenLabs + tools                                          | build local → TestFlight (checklist AGENTS.md)       |

Los workflows registran la task definition actual con nueva imagen: los
secrets nuevos deben existir ANTES en Secrets Manager y estar REFERENCIADOS
en la task definition (un patch único).

## PASO ÚNICO de infraestructura (una vez, por Anthropic-free env vars)

Las nuevas variables viven en el secret `cooppadresd/ai` (Secrets Manager,
key-value) y se inyectan a la task como `secrets` con `valueFrom`
`...secret:cooppadresd/ai:<KEY>::`. Una sola vez (console o CLI):

```bash
aws secretsmanager put-secret-value \
  --secret-id cooppadresd/ai \
  --secret-string file://ai-secret-updated.json   # JSON actual + las 4 claves nuevas

# luego patch de la task definition para REFERENCIAR las claves nuevas
# (secrets[] del contenedor `ai`):
#   ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID, ELEVENLABS_BASE_URL, VOICE_ENABLED
# valueFrom = arn:aws:secretsmanager:us-east-2:933629770820:secret:cooppadresd/ai-XXXXX:<KEY>::
```

Valores de producción (seguros, no en Git): la misma API key de ElevenLabs o
una key dedicada por entorno (recomendado: key `coppadresd-ai-service-prod`
con scopes mínimos); `ELEVENLABS_AGENT_ID` → el agente prod (crear agente
separado con tag `prod` vía MCP — NO reutilizar el de dev); `VOICE_ENABLED=true`.

## Orden de despliegue para TestFlight

1. Commit + push `coppAddresdBack` (rama que dispara `deploy-backend.yml` —
   detecta servicios afectados; API sale por `/api/v1/chat/voice/session`).
2. Push `ai-service` (branch `dev`) → `deploy-ai.yml` espera rollout
   COMPLETED. Verificar `https://erp.coppadresd.com` salud del proxy chat.
   ⚠️ Si la task def aún no tiene los secrets ELEVENLABS_*, el arrancar con
   `VOICE_ENABLED=true` falla: aplicar el patch del paso anterior ANTES.
3. Smoke prod (sin app): login real en `https://erp.coppadresd.com/api/auth/login`
   (aplicación `app`) → `POST /api/v1/chat/voice/session` → 200 con signedUrl.
4. App móvil:
   ```bash
   cd antares-paciente
   npm run sync          # build production + cap sync (env → erp.coppadresd.com)
   # checklist TestFlight de antares-paciente/AGENTS.md (version bump, archive, upload)
   ```
   Verificar que `dist/` NO contiene `sk_` ni la key: `rg -o "sk_[a-z0-9]+" dist/ || echo OK`.
5. Probar en TestFlight: botón de voz → saludo del agente → "¿cuáles son mis
   próximas citas?" (tool real con JWT del device).

## Entornos (FASE 13)

- Local: agente dev (`agent_5501m3w6n1j7fe5tx02291nkvy8n`), `.env` local.
- Prod: agente prod (`agent_1101m3w6pc64e9prz4bkvwct0hy9`) vía
  `ELEVENLABS_AGENT_ID` en Secrets Manager; el endpoint solo emite
  sesiones para el agent_id configurado (allowlist implícita).
- Nunca apuntar dev a prod: la config por entorno define agente y key.
