# Configuración de los agentes ElevenLabs (FASE 6–10)

> **Migración de cuenta (2026-10-01):** Luis cambió de cuenta ElevenLabs. La
> configuración del agente anterior (`agent_4501m3qqzq0ne7qtpcf3p2wkec1a`,
> workspace de diazf7583@gmail.com) se rescató vía hosted MCP ANTES del logout
> y se recreó fielmente en el workspace nuevo (región US) con la API del MCP:
> **8 client tools nuevas + 2 agentes (dev y prod)**. Spec fuente:
> `.coppadresd-context/elevenlabs-migration-2026-10-01/agent-spec-from-backup.md`.

## Agentes

| Campo      | Dev                                      | Prod                                      |
| ---------- | ---------------------------------------- | ----------------------------------------- |
| Nombre     | `Copp Adresd — Asistente Paciente (dev)` | `Copp Adresd — Asistente Paciente (prod)` |
| `agent_id` | `agent_5501m3w6n1j7fe5tx02291nkvy8n`     | `agent_1101m3w6pc64e9prz4bkvwct0hy9`      |
| Tag        | `dev`                                    | `prod`                                    |

Configuración idéntica en ambos. El agente prod se activará cuando el secret
de producción (`cooppadresd/ai`, clave `ELEVENLABS_AGENT_ID`) apunte a él —
nunca reutilizar el dev en prod (regla de `07-deployment.md`).

## Config compartida (verificada post-creación vía agents_get)

- Idioma `es`; espejo estricto es/en con `language_detection`
  (`only_at_conversation_start=false`).
- LLM `gemini-2.5-flash`, `temperature 0.4` (estabilidad anti-babbling; ver
  fix del 2026-09-29 más abajo).
- TTS `eleven_flash_v2_5`, `optimize_streaming_latency 3`,
  `agent_output_audio_format pcm_16000`.
- Voz `cjVigY5qzO86Huf0OWal` — default Conversational AI del workspace (la
  cuenta nueva asignó la misma voz por defecto que la anterior).
- First message: "Hola, soy el asistente de Copp Adresd. ¿Cómo te puedo
  ayudar con tus citas o tu atención de hoy?"
- ASR `scribe_realtime` (quality `high`, `pcm_16000`).
- Turn: `mode turn`, `turn_v3`, `turn_timeout 7`, `silence_end_call_timeout -1`.
- Duración máx. 600 s; file input hasta 10; background sound 0.15.
- Privacidad (FASE 10): `record_voice=false`, `retention_days=30`,
  `delete_transcript_and_pii=true`, `delete_audio=true`,
  `nested_history_redaction=true`, `zero_retention_mode=false` (QA TestFlight;
  revisar con el equipo legal antes de producción definitiva).
- `auth.enable_auth=false`: la emisión de sesiones se controla server-side —
  solo el backend con la API key pide signed URLs; allowlist implícita por el
  `ELEVENLABS_AGENT_ID` configurado por entorno.

## System prompt (reglas clave)

Prompt completo en el agente (rescatado y recreado verbatim; único cambio:
typo "párralo" → "paralo"). Reglas clave: no inventar datos (tools primero),
identidad resuelta por el sistema (nunca conversacional), confirmación
explícita antes de acciones sensibles, no diagnóstico/prescripción, no PHI de
terceros, respuestas cortas habladas, manejo de silencios, derivación a
humano, y emergencias → botón SOS de la app.

### Idioma (fix de comportamiento, 2026-09-29) — conservado en la migración

- **Regla de espejo estricto**: responde en el idioma del paciente (es→es,
  en→en, mezcla→idioma del último turno); jamás cambiar de idioma a mitad de
  frase o entre turnos sin que el paciente lo haga.
- **`language_detection` (built-in system tool) activado** con cambio dinámico
  (`only_at_conversation_start=false`).
- **LLM `gemini-2.5-flash`** + `temperature 0.4` (el default
  `qwen35-397b-a17b` era inestable) + regla anti-babbling.
- Espejo del agente de texto (CoppAI) en `ai-service/app/agents/prompts.py`
  y en el seeder backend (`AgentCatalogSeeder.cs`).

## Client tools (8, recreadas el 2026-10-01)

Todas de tipo **client**: ElevenLabs no llama a ningún endpoint; la app
ejecuta la llamada con su JWT y devuelve el resultado por el mismo canal.
Derivadas de los contratos reales de
`antares-paciente/src/utils/appointmentsApi.ts`.

| Tool (ID nueva en el workspace nuevo)                                                                 | Endpoint real que ejecuta la app                                                                                     |
| ----------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `get_my_upcoming_appointments` (`tool_2301m3w6j4stf0j8vqq8rqz3c4mx`)                                  | `GET /api/v1/appointments/mine`                                                                                      |
| `get_my_requests` (`tool_0501m3w6j57aekrtq2j0nted2hhe`)                                               | `GET /api/v1/telemedicine/requests/mine`                                                                             |
| `list_specialties` (`tool_6101m3w6j54qf5vsm99haqjra17d`)                                              | `GET /api/v1/specialties`                                                                                            |
| `list_professionals` (`tool_1401m3w6j4y5ffaaqbhmsg8yd4t4`)                                            | `GET /api/v1/professionals-catalog`                                                                                  |
| `get_availability_slots(date, specialty_id?, professional_id?)` (`tool_8901m3w6j4zffjyvy3p2dde6ke2d`) | `GET /api/v1/appointments/availability` (organization_id la completa el cliente desde `GET /api/v1/telemedicine/me`) |
| `request_appointment(specialty_id, reason, …)` (`tool_6001m3w6j4zpfphtvj4syk9b1str`)                  | `POST /api/v1/telemedicine/requests`                                                                                 |
| `reschedule_appointment(appointment_id, new_start, …)` (`tool_8501m3w6j4x6ft8tdsv1fyj69hsc`)          | `POST /api/v1/appointments/{id}/reschedule`                                                                          |
| `cancel_appointment(appointment_id, reason)` (`tool_7001m3w6j507e3gb4cf608zv74a8`)                    | `POST /api/v1/appointments/{id}/cancel`                                                                              |

Nombres snake_case de negocio (regla 4 del prompt). La identidad del paciente
SIEMPRE la resuelve el JWT — el agente no recibe ni consulta IDs de paciente.

## Estado de la migración (2026-10-01)

- ✅ MCP re-autenticado (OAuth) con la cuenta nueva — workspace US (inició vacío).
- ✅ 8 client tools + agente dev + agente prod recreados vía MCP.
- ✅ `ai-service/.env`: `ELEVENLABS_API_KEY` + `ELEVENLABS_AGENT_ID` (dev).
- ✅ Prod: secret `cooppadresd/ai` → key nueva + `ELEVENLABS_AGENT_ID` del agente
  prod; `ELEVENLABS_BASE_URL=https://api.us.elevenlabs.io`,
  `VOICE_ENABLED=true` (sin cambios). Redeploy forzado del task
  `cooppadresd-ai` para que el contenedor lea los nuevos valores (los secrets
  se inyectan al arrancar la task).
- ✅ Signed URL real (HTTP 200) verificado para ambos agentes con la key nueva,
  directo contra `api.us.elevenlabs.io`. El E2E full-stack local (voice/session
  vía API .NET) quedó verificado el 2026-09-29 y el código no cambió.
- Key: **1 compartida dev/prod** (decisión de Luis; el runbook recomienda key
  por entorno — revisar antes de TestFlight definitiva). Motivo de la
  migración: la cuenta anterior (free) quedó sin créditos (`quota_exceeded`).

## Histórico (cuenta anterior, abandonada)

- E2E verificado el 2026-09-29: login demo → `POST /api/v1/chat/voice/session`
  → 200 con `signedUrl` real (201 chars) emitido por el agente de entonces.
- Agente anterior: `agent_4501m3qqzq0ne7qtpcf3p2wkec1a`
  (branch `agtbrch_0801m3qqzrkee45vgsknqdvex6az`).
- Tool IDs antiguos (inválidos en el workspace nuevo):
  `tool_7401m3qr1nmpfh9rvy9jnfmqbn5e`, `tool_8001m3qr1nthfw0aykm7hvvd4vrb`,
  `tool_4101m3qr1ntqeh7t697ashygg15a`, `tool_8101m3qr1nw5e169xgm7p99emhje`,
  `tool_8601m3qr1p52fhm873vyvxw48t6f`, `tool_8001m3qr1nskejhbbzjxvqmyfe3n`,
  `tool_4001m3qr1nt6f3pvb6n83c87mgbw`, `tool_2501m3qr1nt2fnsvbqbzcnwmez7g`.
