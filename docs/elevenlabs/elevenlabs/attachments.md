# Attachments del chat (estado y diseño)

> P2 del plan de continuación. Estado real verificado contra el código.

## Imágenes — SOPORTADO hoy (D3, change agente-asistente-citas)

```
Composer [+] → captureChatImage() (@capacitor/camera, CameraSource.Prompt
→ ActionSheet nativa Cámara/Galería, JPEG base64 <1 MB, width 1280)
→ POST /api/v1/chat/stream { message, imageData, imageMimeType }
→ API .NET → ai-service (multimodal) → respuesta
→ burbuja del usuario con miniatura (dataUrl)
```

- Validación de tipos en la app: `image/jpeg`, `image/png` (ChatPage).
- El plugin transcodea HEIC → JPEG (lección del bug TestFlight P1 de comida).
- Base64 en el body es un contrato **existente** con techo de peso (<1 MB,
  redimensionado nativo); no persiste en PostgreSQL (solo viaja al LLM).

## Video / PDF / archivos — DISEÑO, aún no implementado

El backend/ai-service no aceptan video ni documentos en el chat hoy. El
composer muestra las opciones con _progressive disclosure_ pero las no
soportadas responden con un toast amigable (sin pantallas rotas).

Diseño acordado para implementar (no improvisar antes de TestFlight):

```
App → Backend: POST /media/upload-intent (Storage module, S3 presigned)
    → PUT directo a S3 (presigned URL, progreso/retry/cancelación)
    → metadata en BD (tipo, tamaño, dueño = JWT, anti-IDOR)
    → mensaje de chat con attachment_id (NO base64)
Backend → ai-service: para PDFs reutilizar el pipeline de texto de
    lab-exam (extracción) como contexto del turno; video → thumbnail+link.
```

Requisitos de la UI ya definidos en el plan: preview en chat (imagen),
card con icono/nombre/peso/tipo (archivo), thumbnail+duración (video),
progreso, retry, cancelación y errores amigables.

## Llamadas (Twilio Voice / VoIP)

**Diferencia explícita** (regla del plan):

- **A) Conversación de voz con el agente IA** (ElevenLabs) → implementado
  (signed URL + client tools + JWT).
- **B) Llamada telefónica/VoIP real** → NO existe infraestructura VoIP
  entrante hoy (Twilio en el ecosistema cubre SMS/Verify + salas de video
  de telemedicina, no llamadas al paciente). Implementarla requiere Twilio
  Voice + PushKit + CallKit + entitlements de VoIP + APNs background —
  riesgo alto para el build de TestFlight: **documentado como fase futura**
  (ver `04-twilio-integration.md`), no improvisada.
