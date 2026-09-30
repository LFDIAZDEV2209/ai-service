# iOS / TestFlight — checklist del release

> P0 del plan. Verificado contra el repo `antares-paciente`.

## Permisos y config (ya presentes, no tocar)

- `NSMicrophoneUsageDescription` ✔ en `ios/App/App/Info.plist` (commiteado;
  el CLI de Capacitor 8 NO aplica `ios.infoPlist` — el plist es la fuente).
- Cámara/fotos ✔ (mismo plist). Bluetooth/wearable ✔.
- **NO agregar** el bloque ATS de desarrollo (`NSAllowsArbitraryLoads`) al
  archivar: `npm run release:check` lo valida.

## Build de TestFlight (flujo del AGENTS.md de la app)

```bash
# desde repos limpios y checks verdes (lint/test/build/i18n)
npm run sync          # vite build --mode production + npx cap sync ios
                      # .env.production → https://erp.coppadresd.com
```

1. Confirmar que `dist/` NO contiene la API key: `rg "sk_" dist/ || echo OK`.
2. Bump de versión: `cd ios/App && xcrun agvtool new-version -all <N>`.
3. Xcode: scheme `App` → Any iOS Device → Archive → Distribute App →
   App Store Connect → subir → esperar Processing → grupo de testers.
4. No cambiar Team / Bundle Identifier / Provisioning / Capabilities.

## Verificaciones de comportamiento en device

- Login real → Chat → botón de voz → agente saluda (Conectando → Escuchando).
- Hablar: transcripción + respuesta por voz; interrumpir al agente (barge-in).
- Tool real: "¿cuáles son mis próximas citas?" → datos reales con JWT.
- Mute, colgar, reabrir; respuesta con imagen adjunta; modo avión → mensaje
  amigable (nunca errores técnicos en pantalla).
- Fallback: si ElevenLabs no conecta, Web Speech toma el relevo o el overlay
  ofrece chat de texto.

## Riesgos restantes (documentados, no bloqueantes)

- `zero_retention_mode` aún false (QA de TestFlight): revisar con legal.
- Video/PDF en chat: composer muestra opciones; las no soportadas responden
  con toast amigable (diseño completo en `attachments.md`).
- VoIP entrante real (CallKit/PushKit): fase futura con Twilio Voice.
