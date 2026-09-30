# Configuración del agente ElevenLabs (FASE 6–7)

Creado el 2026-09-29 vía hosted MCP de ElevenLabs. Workspace región US.

> Verificación E2E (FASE 12): login demo → `POST /api/v1/chat/voice/session`
> → 200 con `signedUrl` real de ElevenLabs (201 chars) emitido por este
> agente, sin exponer la API key.

## Agente

| Campo         | Valor                                                                                              |
| ------------- | -------------------------------------------------------------------------------------------------- |
| Nombre        | `Copp Adresd — Asistente Paciente (dev)`                                                           |
| `agent_id`    | `agent_4501m3qqzq0ne7qtpcf3p2wkec1a`                                                               |
| Branch        | `agtbrch_0801m3qqzrkee45vgsknqdvex6az`                                                             |
| Tag           | `dev`                                                                                              |
| Idioma        | `es`                                                                                               |
| TTS           | `eleven_flash_v2_5` (exigido por la plataforma para agentes no-ingleses; menor latencia)           |
| Voz           | `cjVigY5qzO86Huf0OWal` (voz Conversational AI por defecto del workspace)                           |
| First message | "Hola, soy el asistente de Copp Adresd. ¿Cómo te puedo ayudar con tus citas o tu atención de hoy?" |

> Cambiar el nombre al pasar a producción (tag `prod`) — no reutilizar el
> agente dev. El `agent_id` de producción será otro y vivirá en config por
> entorno, nunca en el código.

## System prompt (resumen de reglas)

Prompt completo en el agente. Reglas clave: no inventar datos (tools primero),
identidad resuelta por el sistema (nunca conversacional), confirmación explícita
antes de acciones sensibles, no diagnóstico/prescripción, no PHI de terceros,
respuestas cortas habladas, manejo de silencios, derivación a humano, y
emergencias → botón SOS de la app.

### Idioma (fix de comportamiento, 2026-09-29)

Síntomas reportados: el agente a veces cambiaba a inglés a mitad de la
conversación, se trababa o decía frases sin sentido. Correcciones aplicadas:

- **Regla de espejo estricto** en el system prompt: responde en el idioma del
  paciente (es→es, en→en, mezcla→idioma del último turno); jamás cambiar de
  idioma a mitad de frase o entre turnos sin que el paciente lo haga.
- **`language_detection` (built-in system tool) activado** con cambio dinámico
  (`only_at_conversation_start=false`).
- **LLM cambiado de `qwen35-397b-a17b` (default del workspace, inestable) a
  `gemini-2.5-flash`** + `temperature 0.4` — estabilidad de discurso y menor
  babbling; regla anti-babbling ("si te pierdes, párralo y pregunta").
- Versión del agente con `version_description` del fix.
- **Agente de texto (CoppAI)**: regla 1 del prompt base reforzada en
  `ai-service/app/agents/prompts.py` y en el espejo del seeder backend
  (`AgentCatalogSeeder.cs`) — mismo contrato de idioma es/en.

## Client tools registradas (FASE 7)

Todas de tipo **client**: ElevenLabs no llama a ningún endpoint; la app ejecuta
la llamada con su JWT y devuelve el resultado por el mismo canal. Derivadas de
los contratos reales de `antares-paciente/src/utils/appointmentsApi.ts`.

| Tool                                                            | Endpoint real que ejecuta la app                                                                                     |
| --------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `get_my_upcoming_appointments`                                  | `GET /api/v1/appointments/mine`                                                                                      |
| `get_my_requests`                                               | `GET /api/v1/telemedicine/requests/mine`                                                                             |
| `list_specialties`                                              | `GET /api/v1/specialties`                                                                                            |
| `list_professionals`                                            | `GET /api/v1/professionals-catalog`                                                                                  |
| `get_availability_slots(date, specialty_id?, professional_id?)` | `GET /api/v1/appointments/availability` (organization_id la completa el cliente desde `GET /api/v1/telemedicine/me`) |
| `request_appointment(specialty_id, reason, …)`                  | `POST /api/v1/telemedicine/requests`                                                                                 |
| `reschedule_appointment(appointment_id, new_start, …)`          | `POST /api/v1/appointments/{id}/reschedule`                                                                          |
| `cancel_appointment(appointment_id, reason)`                    | `POST /api/v1/appointments/{id}/cancel`                                                                              |

Tool IDs:
`tool_7401m3qr1nmpfh9rvy9jnfmqbn5e`, `tool_8001m3qr1nthfw0aykm7hvvd4vrb`,
`tool_4101m3qr1ntqeh7t697ashygg15a`, `tool_8101m3qr1nw5e169xgm7p99emhje`,
`tool_8601m3qr1p52fhm873vyvxw48t6f`, `tool_8001m3qr1nskejhbbzjxvqmyfe3n`,
`tool_4001m3qr1nt6f3pvb6n83c87mgbw`, `tool_2501m3qr1nt2fnsvbqbzcnwmez7g`

Nombres **snake_case de negocio** (no genéricos tipo `query_database`), según
regla 4 del prompt. La identidad del paciente SIEMPRE la resuelve el JWT — el
agente no recibe ni consulta IDs de paciente.

## Pendiente de configuración (requiere humano)

- **API key** ✔ configurada en `ai-service/.env` (local). Producción:
  Secrets Manager `cooppadresd/ai` + task def (ver `07-deployment.md`).
- FASE 10 (privacidad) ✔ aplicada vía MCP: `record_voice=false`,
  `retention_days=30`, `delete_transcript_and_pii=true`, `delete_audio=true`.
  `zero_retention_mode` sigue false para QA de TestFlight — revisar con el
  equipo legal antes de producción definitiva.
- FASE 9 (Twilio): tools webhook si se reutiliza el agente en llamadas.
