> TestFlight restaurado: `VOICE_REVIEW_VERIFICATION_ENABLED=false` por defecto
> utiliza el agente ElevenLabs configurado, sin exigir los pins pendientes del
> proceso de revisión. No modifica el agente remoto. `VOICE_ENABLED`, credenciales
> del proveedor y autenticación interna siguen siendo obligatorios. Activar la
> verificación al retomar revisión requiere configurar versiones y herramientas.

# Configuración de los agentes ElevenLabs (FASE 6–10)

> **Alcance vigente de primera publicación:** leer
> [VITA_PATIENT_SCOPE.md](../VITA_PATIENT_SCOPE.md) antes de configurar/desplegar.
> Las referencias siguientes a prompt clínico, PATCH de idioma y E2E corresponden
> al historial previo; no acreditan las nuevas versiones ES/EN ni sus herramientas.

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

## Voz más humana (2026-11-06) — APLICADO a dev y prod vía MCP

Feedback del owner: la voz sonaba robótica. Versiones nuevas publicadas en
ambos agentes (dev `agtvrsn_4101m6rqw7gqfaa8crjtrkc1d07b`, prod `agtvrsn_9101m6rqxxvpeaqs1bq5384k3416`):

- **TTS `eleven_flash_v2_5` → `eleven_v4_turbo`** (default actual de la
  plataforma para agentes; mucho más natural que flash, latencia aún apta para
  conversación en vivo). El flash era la exigencia de la plataforma de
  sept-2025 para agentes no-ingleses — vigente ya NO aplica.
- **`stability 0.5 → 0.35`** (más variación expresiva = menos tono recitado).
- `speed 1.0`, `similarity_boost 0.8`, voz, formato `pcm_16000` y ASR: sin cambios.
- **Prompt, regla 6** (parche parcial de `prompt.prompt`; LLM/tools intactos):
  añadida la cláusula de naturalidad "Suena a humano, no a sistema: palabras
  del día a día, calidez genuina y ritmo pausado; cuando des un dato
  (profesional, día, hora), intégralo dentro de una frase en lugar de
  recitarlo como una lista." Regla anti-babbling conservada.
- **Rollback**: reponer `model_id eleven_flash_v2_5`, `stability 0.5`, y la
  regla 6 sin la cláusula de naturalidad (histórico en versiones del agente).

## Respuesta fluida (2026-11-08) — reasoning medium → low en ambos agentes

Síntoma en demo: el agente demoraba mucho en empezar a hablar. Analytics del
agente prod (`agents_query_analytics` metric latency): TTS sano
(`TTS_SERVICE_TTFB` ~0.08 s, VAD ~0.3 s); el problema era **`LLM_SERVICE_TTFB`**
— history ~1.1-1.9 s y hoy colas de 4.9-8.6 s p95 (17:00Z un turno de 8.63 s) +
`LLM_CASCADE_FALLBACK_USED` desde gemini-3.5-flash (degradación del proveedor).
Con la migración owner → `gemini-3.8-flash` el agente quedó
implícitamente con `reasoning_effort medium`, que mete "pensamiento" antes del
primer token.

- **Fix**: `agents_update_prompt_settings` con `reasoning_effort: low` en prod
  (`agtvrsn_7101m6y5mk8qfyt8f58aa0aq1d3k`) y dev (tras migrar su LLM
  2.5-flash → 3.8-flash + low en el mismo PATCH). Prompt/tools/temperatura 0.4
  intactos (verificados vía agents_get).
- `service_tier "fast"` NO es soportado por gemini-3.8-flash (400) — tier queda
  `standard`. `enable_reasoning_summary` queda false (ya estaba).
- Si la latencia vuelve a degradarse (provider-side), palanca siguiente:
  bajar a `reasoning_effort: none` o cambiar LLM a `gemini-3.5-flash` /
  `gemini-3.7-flash`. Los changes de config aplican a la SIGUIENTE sesión
  (las en vuelo conservan la config con la que abrieron).
- Rollback: `reasoning_effort: medium`; dev además volver a `gemini-2.5-flash`.

## Config compartida (verificada post-creación vía agents_get)

- Idioma `es` (fallback estático; desde 2026-10-19 el idioma real de cada
  sesión se fija por PATCH por sesión) — el prompt ya NO fuerza espejo
  (`language_detection` sigue activa como tool del sistema).
- LLM `gemini-3.8-flash` + `reasoning_effort low` (desde 2026-11-08; ver
  "Respuesta fluida" abajo; `gemini-2.5-flash` quedó deprecated y lo migró el
  owner desde el dashboard el 2026-11-07), `temperature 0.4` intacta
  (estabilidad anti-babbling; ver fix del 2026-09-29 más abajo).
- TTS `eleven_v4_turbo` (desde 2026-11-06; antes `eleven_flash_v2_5`),
  `agent_output_audio_format pcm_16000`, `stability 0.35`. Ver sección
  "Voz más humana" arriba.
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
terceros, respuestas cortas habladas, manejo de silencios, emergencias → botón
SOS de la app. **Actualizado 2026-10-19 (ver detalle más abajo)**: sin
derivación a humano (canal inexistente; 100% CoppAI) y el idioma lo decide el
`language` de la sesión, no el espejo del paciente.

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
- **PATCH server-side por sesión (2026-10-19, reemplaza el override del
  cliente)**: con sesiones por signed URL los overrides del cliente NO
  aplican de forma fiable (commit `8a7b746` en antares-paciente quitó
  `overrides.agent.language` del `startSession` por eso). Mecanismo actual:
  la app envía `language` ('es' | 'en') en el request de voz
  (`POST /api/v1/chat/voice/session`) → backend lo propaga
  (`VoiceSessionInternalRequest.language`) → el AI Service hace
  `PATCH /v1/convai/agents/{agent_id}` con
  `conversation_config.agent.{language, first_message}` ANTES de emitir el
  signed URL (`set_agent_language` en `app/voice/client.py`; saludo
  localizado es/en). Fail-open: un fallo del PATCH no rompe la sesión (el
  agente sigue con su config del dashboard). Elección de diseño: UN agente
  mutado por sesión — dos sesiones concurrentes en idiomas distintos pueden
  competir por la config (carrera benigna: la sesión lee la config al
  conectar). Un cambio de idioma a mitad de llamada de voz toma efecto en
  la siguiente sesión.
- **System prompt actualizado (2026-10-19, APLICADO en ambos agentes vía
  `PATCH /v1/convai/agents/{agent_id}` con
  `conversation_config.agent.prompt.prompt`; parche parcial — el resto del
  objeto `prompt`, incl. los client tools, queda intacto)**: la sección IDIOMA
  ya no dice "La conversación inicia en español" ni usa regla de espejo —
  delega en el `language` de la sesión ("el idioma lo decide la app, no la
  conversación"); las reglas 1/5/8 ya NO ofrecen derivar a humano: la atención
  es 100% asistida por CoppAI y se ofrece reintentar más tarde o agendar cita
  (`suggest_appointment`). El dashboard conserva `language: es` y el
  `first_message` en español SOLO como fallback estático (aplica si el request
  llega sin `language`). El equivalente para los agentes de TEXTO sigue siendo
  `build_turn_policy` en `app/agents/prompts.py`.

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
