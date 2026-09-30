# 08 — Testing

> FASE 12 del plan. Qué está automatizado y qué se prueba a mano.

## Automatizado (ejecutado y pasando)

| Suite                                                       | Alcance                                                                                                                                                                              | Estado |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------ |
| `ai-service` `uv run pytest`                                | 194 tests — incluye `tests/test_voice.py`: auth X-Internal-Key, 503 con voz apagada, contrato feliz (MockTransport de httpx), 401 de ElevenLabs → mensaje seguro, upstream 500 → 502 | ✅     |
| `ai-service` `uv run ruff check .`                          | lint                                                                                                                                                                                 | ✅     |
| backend `dotnet test CoppAddresd.UnitTests`                 | 1002 tests — incluye `VoiceSessionCommandHandlerTests` (handler+validator) y fake de contrato actualizado                                                                            | ✅     |
| app `npm test -- --run`                                     | 855 tests (100 archivos)                                                                                                                                                             | ✅     |
| app `npm run lint` / `npm run build` / `npm run i18n:check` | oxlint + tsc + vite + claves i18n                                                                                                                                                    | ✅     |

## Cobertura por pieza

- **Backend .NET**: validador (UserId del JWT requerido, ThreadId acotado),
  handler (propaga DTO+CT al cliente IA), endpoint con mapeo de errores
  (502 upstream, 503 offline, mensaje amigable — sin trazas internas).
- **ai-service**: endpoint interno, flag `VOICE_ENABLED`, timeouts de red,
  códigos de ElevenLabs (401/403/404/5xx), jamás filtra el signed URL.
- **App**: suites existentes (chat, citas) intactas; VoiceOverlay mantiene el
  comportamiento D4 de fallback (tests de consolidación de voz siguen verdes).

## E2E del flujo completo (verificado en desarrollo)

```
login demo → JWT aud=app
→ POST :5080/api/v1/chat/voice/session
→ Gateway → API .NET (MediatR → AiServiceClient, X-Internal-Key)
→ ai-service → ElevenLabs get-signed-url
→ 200 { signedUrl (201 chars), agentId, conversationId? }
```

Pendiente en device (TestFlight): conexión WS del SDK, permiso de micrófono
iOS, tools en vivo (citas reales), reconexión tras corte.

## Prueba manual recomendada por device (TestFlight)

1. Login real → botón de voz → el agente saluda (Conectando → Escuchando).
2. "¿Cuáles son mis próximas citas?" → tool `get_my_upcoming_appointments`
   ejecutada en el device → respuesta por voz con datos reales.
3. "Quiero cancelar mi cita" → el agente pide confirmación → tool
   `cancel_appointment` con motivo dictado.
4. Mute / colgar / reabrir; modo avión → degradación Web Speech o mensaje
   amigable (nunca un error técnico en pantalla).
