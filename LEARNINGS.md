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

## Integrar dev sin perder el alcance ni la recuperación del chat

El merge de noviembre conserva el routing por perfil/RAG, la consulta de contexto
del paciente, la detección de idioma por turno y la omisión de temperature para
modelos que lo rechazan. La política educativa sigue siendo el primer bloque
de sistema. Al añadir instrucciones de idioma, localizar las referencias por rol
y propósito en las pruebas; su posición numérica deja de ser estable. El analizador
conserva metadatos y reintento de formato de dev, con la política antes de cada
intento y validación de códigos de evidencia.
