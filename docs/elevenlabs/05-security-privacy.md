# 05 — Seguridad y privacidad (entorno médico)

> FASE 4/5/10 del plan. Reglas implementadas + decisiones pendientes.

## Modelo de seguridad implementado

```
Paciente autenticado (JWT aud=app)
→ Gateway :5080
→ POST /api/v1/chat/voice/session   [Authorize]  ← identidad SOLO del JWT
   · FluentValidation (UserId del JWT, ThreadId ≤200)
   · auditoría estructurada (UserId, AgentId, ConversationId — sin signed URL)
→ ai-service /internal/voice/session (X-Internal-Key)
   · ELEVENLABS_API_KEY solo allí (.env git-ignored / Secrets Manager prod)
→ ElevenLabs GET /convai/conversation/get-signed-url
→ app conecta con signedUrl (credencial temporal de vida corta, no persistida)
→ client tools: el DISPOSITIVO ejecuta con su propio JWT → endpoints .NET
```

- La API key nunca está en frontend/APK/IPA/build ni en logs.
- El signed URL es one-shot para observabilidad; nunca se persiste ni se
  loguea (ni completo ni truncado) en backend ni ai-service.
- El agente NO recibe el JWT ni el id del paciente; las tools aportan los
  datos. Un paciente malicioso no puede pedir datos de otro: sus tools se
  ejecutan con SU token.
- Cambiar de cuenta ElevenLabs = cambiar `ELEVENLABS_API_KEY` +
  `ELEVENLABS_AGENT_ID` (+ base URL si cambia la región). Sin tocar código.

## Secretos (FASE 5)

| Entorno    | Dónde                                                                                                                |
| ---------- | -------------------------------------------------------------------------------------------------------------------- |
| local      | `ai-service/.env` (git-ignored): `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`, `ELEVENLABS_BASE_URL`, `VOICE_ENABLED` |
| producción | Secrets Manager `cooppadresd/ai` (mismas claves) + referencias en la task definition ECS (ver 07-deployment)         |

En producción el validador de config exige key+agent si `VOICE_ENABLED=true`
(arranca falla clara, sin exponer valores). El endpoint responde 503 si el
módulo está apagado.

## Privacidad (FASE 10) — aplicada vía MCP al agente dev

| Setting                     | Valor                      | Justificación                                                                                                                         |
| --------------------------- | -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `record_voice`              | **false**                  | No se graba/almacena audio del paciente                                                                                               |
| `retention_days`            | **30**                     | Transcripts solo para QA/debug, se borran                                                                                             |
| `delete_transcript_and_pii` | **true**                   | PII fuera del historial                                                                                                               |
| `delete_audio`              | **true**                   | Redundancia sobre `record_voice`                                                                                                      |
| `zero_retention_mode`       | false (decisión pendiente) | Se conserva transcript 30d para QA de TestFlight; revisar con el equipo legal/compliance si el producto exige zero retention estricta |

## Qué viaja a ElevenLabs (transparencia técnica)

- Audio del micrófono del paciente durante la conversación (no se guarda del
  lado ElevenLabs según la config anterior) y transcript (30 días).
- Respuestas de las client tools: datos que el paciente ve igual en la app
  (sus citas, especialidades, profesionales públicos). Fechas preformateadas
  `dd/mm/aaaa` — sin ids UUID innecesarios donde evitable.
- NO viajan: JWT, ids de pacientes, historial clínico, resultados, ni datos
  de terceros (el prompt del agente lo prohíbe explícitamente).

## Pendiente de decisión con el equipo (no asumido)

- HIPA/consentimiento informado del paciente para uso de voz (UI: banner de
  consentimiento antes del primer uso — por agregar en la app cuando el
  legal lo defina).
- Región de datos: workspace ElevenLabs actual está en **US**; si se exige
  residencia UE, migrar workspace a `api.eu.residency...` (solo cambia
  `ELEVENLABS_BASE_URL`).
- Retención: revisar `zero_retention_mode=true` para producción si QA ya no
  necesita transcripts.
