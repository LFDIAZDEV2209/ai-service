# 09 — Troubleshooting

| Síntoma                                                    | Causa probable                                              | Solución                                                                                                                               |
| ---------------------------------------------------------- | ----------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| OAuth del MCP: `Protected resource ... does not cover ...` | URL del MCP no coincide con la región del workspace         | Registrar `https://api.us.elevenlabs.io/v1/mcp` (o la región que anuncia el error) en `opencode.json` global; reautenticar con `/mcps` |
| `opencode mcp list` → `elevenlabs needs authentication`    | OAuth vencido o nunca hecho                                 | TUI → `/mcps` → elevenlabs → iniciar sesión OAuth                                                                                      |
| `POST /chat/voice/session` → 503 `Voz deshabilitada`       | `VOICE_ENABLED=false` en el entorno                         | Activar en `.env` (local) o Secrets Manager + task def (prod)                                                                          |
| 503 `Configuración de voz incompleta`                      | Falta `ELEVENLABS_API_KEY` o `ELEVENLABS_AGENT_ID`          | Completar la sección ElevenLabs del `.env` / secret `cooppadresd/ai`                                                                   |
| 401/403 desde ElevenLabs al emitir sesión                  | API key inválida o sin scopes (Agents)                      | Crear key con permisos Conversational AI/Agents; verificar cuenta activa                                                               |
| 404 "El agente no existe"                                  | `ELEVENLABS_AGENT_ID` de otra cuenta/región                 | Usar el agent_id del mismo workspace que la key                                                                                        |
| El agente arranca pero las tools no responden              | App anterior sin `voiceApi.ts` / backend sin el endpoint    | Redesplegar app y API; el agente dice honestamente que no puede (comportamiento esperado con agente viejo)                             |
| Permiso de micrófono denegado (iOS)                        | `NSMicrophoneUsageDescription` ausente                      | Está en el Info.plist commiteado; si se eliminó, restaurar                                                                             |
| Web Speech fallback no disponible (WebView viejo)          | `SpeechRecognition` no existe en el WebView                 | Esperado; el overlay muestra el aviso D4 y ofrece chat de texto                                                                        |
| `database "test" does not exist` al correr `pytest`        | Tests que no limpian cache de settings ejecutan migraciones | Los tests de voz fijan `AUTO_MIGRATE=false`; para suites que no, subir compose (`docker compose up -d`) o usar la BD de test del CI    |
| Cambio de cuenta ElevenLabs                                | —                                                           | Solo: nueva `ELEVENLABS_API_KEY` + `ELEVENLABS_AGENT_ID` (+ `ELEVENLABS_BASE_URL` si cambia región). Sin código                        |

## Logs

- Backend: correlation id + `Sesión de voz` (UserId/AgentId/ConversationId —
  sin signedUrl).
- ai-service: `Voice session` (agent_id, fallos con status de ElevenLabs).
- CloudWatch prod: `/ecs/cooppadresd-ai`, prefix `ai`.
