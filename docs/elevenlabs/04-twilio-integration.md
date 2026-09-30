# 04 — Twilio (fase futura)

> FASE 9 del plan. La integración Twilio existente (Auth OTP Verify SMS,
> Telemedicine video/salas, SOS SMS) **no se modifica** en esta fase.

## Situación

Twilio hoy cubre: OTP por SMS (Auth), salas virtuales (Telemedicine), SOS
(Auth/SOS). El agente de voz ElevenLabs v1 vive solo en la app (client tools
ejecutadas por el dispositivo con JWT).

## Reutilización del mismo agente en llamadas telefónicas (diseño futuro)

Para atender llamadas con el mismo agente hacen falta **webhook tools** (no
client tools, porque no hay dispositivo que las ejecute):

1. Migrar las 8 tools a tipo `webhook` apuntando a endpoints públicos del
   backend (`https://erp.coppadresd.com/api/v1/...`) con header de
   autenticación de servicio (secret del tool, gestionado en ElevenLabs).
2. Identidad del paciente: NO puede venir de un JWT de app. Opciones a
   evaluar entonces: verificación de identidad por OTP en la llamada, o
   `dynamic_variables` firmados en la conversación iniciada por el backend
   (token de vida corta emitido server-side; el tool call lo devuelve y el
   backend valida firma+expiración). **Decisión pendiente** — documentar en
   esta fase con grill de seguridad.
3. La app móvil (client tools) y las llamadas (webhook tools) pueden
   coexistir: ElevenLabs admite mezclar tipos de tool por agente/branch.
4. Número Twilio: comprar/asignar y conectar el agente (FASE 9 real) cuando
   el producto lo pida.

## Qué NO hacer hoy

- No exponer endpoints públicos de business tools solo por adelantar la
  fase: la v1 no los necesita (client tools con JWT bastan y son más
  seguras).
- No tocar la integración Twilio existente (OTP/video): cero cambios.
