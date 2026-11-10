# AI service — aprendizajes

## Versionar la conversación no congela las herramientas de voz

ElevenLabs permite consultar y firmar la misma `version_id`, pero las definiciones
referenciadas por `tool_ids` y opciones de plataforma siguen siendo mutables.
Una comparación de prompt/version_id no basta: resolver la definición completa,
comprobar tipo/nombre y huella revisada antes de cada nueva credencial. No usar un
PATCH de idioma sobre un agente compartido: sesiones simultáneas ES/EN se cruzan.
Las comprobaciones no son atómicas con una sesión ya iniciada; restringir permisos
y revisar cambios del proveedor sigue siendo necesario.

## No elevar memoria ni configuración ERP a autoridad sobre el alcance

Perfiles editables y mensajes de sistema históricos pueden contradecir la política
actual. El alcance del paciente se construye en código cada vez que se invoca al
modelo, también tras herramientas; las referencias editables van en rol de datos.
Un test con modelo falso demuestra la composición, nunca obediencia clínica de un
proveedor real. Requiere evaluación de respuestas desplegadas con datos sintéticos.
