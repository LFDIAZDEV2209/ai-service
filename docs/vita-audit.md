# Auditoría de Vita — 14-11-2026

Propuesta canónica: `openspec/changes/vita-asistente-integral/` en la raíz del
workspace. Este informe distingue evidencia de las capacidades aún propuestas.

## Aislamiento y coordinación

AI: `feature/vita-app-orchestration`, base `a576191` de origin/dev, worktree
`.worktrees/vita-ai`. App: `feature/vita-chat-ux`, base F3 `2f85790`, worktree
`.worktrees/vita-app`. Fetch de los repos completado; AI dev ya estaba al día.
El documento de configuración de voz de la sesión anterior permanece modificado
en el checkout AI principal; se conserva intacto y no se arrastra silenciosamente.

El backend F2 contiene cambios ajenos y avanza: de `5ef2567` a `253b346` durante
la auditoría, relacionados con preediciones y backfill futuro. La app F3 incorpora
Foto IA `2f85790`; F2 incluye descanso y agenda futura. No se cambia su código ni
tasks.md. Herdr no se inspeccionó: falta `HERDR_ENV=1`; la skill prohíbe operar
desde fuera. El estado vivo de tab 1 sigue sin verificar.

## Hallazgos

| Evidencia | Implicación |
| --- | --- |
| `app/orchestration/router.py`: keywords de cuatro perfiles fijos | Un nuevo dominio/agente del ERP no entra por descubrimiento automático |
| `app/agents/resolver.py`: slug→GUID, precedencia de config GUID y RAG por perfil | Aprovechar las correcciones ya desplegadas, no repetirlas |
| `app/agents/knowledge_scope.py`: globales activas + propias, siempre IDs explícitos | Base de aislamiento ya existente; ampliar pruebas de documento archivado y canal |
| `app/agents/runtime_registry.py`: caché por tipo e invalidación al activar | Reutilizar compilación/versionado; unificar elección del runtime para evitar rutas divergentes |
| `app/graph/graph.py`: grafo reactivo agent/tools y memoria opcional | Los subgrafos de referencia no acreditan orquestación de todos los especialistas |
| `app/graph/nodes.py`: el perfil cambia prompt/tools pero el nodo liga herramientas al modelo construido del grafo | Revisar que el agente elegido respete también su modelo/configuración del ERP en la ruta general |
| `app/tools/patient_context.py`: una herramienta de contexto, timeout total 8 s | Acceso parcial y grueso; faltan capacidades específicas para tareas, tests, podcasts y ejercicio |
| Backend `GetPatientAiContextQuery.cs`: comidas del día, medidas, dispositivos y diagnóstico | No equivale a toda la app; vigencia/menú se resuelven con lógica del backend |
| Runtime `tools=[]` significa todas | Registrar nuevas escrituras sin política explícita las expondría a agentes legados |
| `app/graph/nodes.py`: política permite citas salvo WhatsApp; registro contiene appointment | Explica que el chat de la captura siga ofreciendo citas aunque la voz ya no lo haga |
| App `services/chat/chat-service.ts`: POST chat sin streaming | El backend tiene SSE pero la pantalla no recibe el primer texto progresivamente |
| App `utils/chatNutritionVisibility.ts`: flag independiente de nutrición del chat | Capacidades/atajos deben respetar disponibilidad real del cliente y coordinar la activación |
| App `components/VoiceOverlay.tsx`: modal inmersivo, un userLine/botLine y espejo solo de pares completos | La transcripción no se ve progresivamente en el hilo; no acredita persistencia de voz |
| Callback de voz interpreta `type/user_transcript/agent_response`; el SDK instalado entrega `message`, `role/source`, `event_id` | Incompatibilidad comprobada: el manejador ignora los callbacks normalizados; corregir antes de extender el espejo |
| App `ChatPage.tsx` y `global.css`: cabecera ≥195 px + safe area, accesos ≥83 px, retrato grande y múltiples overrides | Reducir estructura fija y ordenar cascada específica del chat, conservando marca y componentes Ionic |

## Atasco reportado de voz

La última llamada real revisada (`conv_9301m7d4vepee81bd0jca7a6f2mv`, 169 s)
contiene siete respuestas de usuario con primer audio entre 0.952 y 1.504 s,
TTFB de modelo entre 0.516 y 0.762 s, TTS entre 0.132 y 0.154 s y una respuesta
marcada interrumpida. No hay error de proveedor registrado. Una interrupción
no demuestra por sí sola un fallo: falta correlación con micrófono, red y cliente.
No se atribuye una causa ni se declara resuelto el atasco.

## Fallo confirmado del puente de transcripción

`VoiceOverlay.tsx:327` interpreta `onMessage` como un evento de socket crudo,
pero `@elevenlabs/react` 1.16.0 reutiliza el callback del cliente 1.26.0.
`client/dist/types.d.ts:43` declara `MessagePayload` con `message`, `event_id`,
`response_id?`, `source` y `role`; `BaseConversation.js:175` entrega la
transcripción con ese formato. El manejador actual no entra en ninguna rama.
La documentación del [SDK oficial](https://github.com/elevenlabs/packages/blob/main/packages/client/src/types.ts)
coincide con la instalación local. No es necesario cambiar de proveedor para
corregir esta incompatibilidad. El mirror además espera un par completo, así
que debe sustituirse por actualización independiente por emisor/utterance.

Identidad de voz actualizada por instrucción directa de Luis, sin alterar turnos
o TTS: dev `agtvrsn_6901m7d6nk2hfnmrb43v24s39z2b`, prod
`agtvrsn_7301m7d6nn7wf0fvjqr82tcjxwee`. Saludos ES/EN se presentan como Vita;
herramientas de citas siguen desenlazadas. Este ajuste menor es independiente
de la futura integración con el runtime.

## Investigación y siguiente entrega

Se consultaron los documentos oficiales de LangChain/LangGraph, ElevenLabs y
OpenAI indicados en design.md. Recomendación: motor compartido con adaptadores
de canal, routing rápido, contexto mínimo, consulta selectiva de documentos y
capabilidades explícitas. No añadir una cadena obligatoria de varios modelos a
cada pregunta sencilla. La transcripción y el streaming se integran por IDs de
mensaje/utterance, no por igualdad de texto ni por pares acoplados.

La propuesta requiere aprobación antes de implementar por la regla OpenSpec del
AGENTS.md raíz. No se han escrito capacidades o UX de producto ni se ha cambiado
el motor de producción. Los worktrees quedan listos para ejecutar el plan.

