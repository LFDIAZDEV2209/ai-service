# Runtime Vita — implementación local

## Corrección de orquestación y documentación — 14-11-2026

La voz legacy de ElevenLabs carecía de tools de la app y su prompt negaba acceso
a datos personales. El modo integral usa Custom LLM: las seis tools autorizadas y
`retrieve_knowledge` se ejecutan en el runtime común, no como webhooks duplicados
en el proveedor. El panel ElevenLabs muestra el Custom LLM y la tool nativa de idioma;
la evidencia de las tools de datos está en la ejecución del grafo y sus fuentes.

La selección de especialista ya no se descarta al detectar una capacidad personal.
Palabras como test/rutina/ejercicio por sí solas no autorizan consultar una ficha:
las preguntas sobre protocolos/documentación siguen la ruta documental. Se mantienen
los canales y el catálogo autorizado de BACK; un agente creado pero no autorizado
para el paciente no se puede seleccionar por su nombre.

Para temas de un especialista se prepara `retrieve_knowledge` antes de una sola
generación de respuesta, tanto en texto como en voz. Se respeta `retrieval.enabled`
y el alcance actual de KBs activas propias + Global; el fallback general solo consulta
Global. Los documentos deben estar `Listo` y su KB `Activo`, con chunks indexados.
Saludos, identidad y ayuda de servicio no hacen RAG ni cargan datos personales.
No se afirma que una fuente fue consultada sin fragmentos devueltos en ese turno.

ES/EN usa `language_detection` mediante function calls OpenAI. El ciclo de idioma
precede al recibo del turno y no genera otro mensaje del paciente ni otra respuesta.
Solo se admite el retorno de esa tool del proveedor; sus prompts, modelos y otras
tools no configuran el runtime ni conceden acceso. El idioma de intervenciones claras
se conserva para respuestas neutras como «OK».

Regresión local: 698 pruebas correctas, 2 PostgreSQL omitidas por no disponer de su
URI aislada en ese comando, 32 avisos heredados. Casos nuevos cubren documentación
ES/EN, especialista y fallback Global, saludos sin RAG y ciclo de idioma sin doble
generación. Esto no acredita por sí solo contenido, asignaciones o audio en producción.

Rama `feature/vita-app-orchestration`, base `cbbe9e2` sobre `a576191`. Contrato
canónico del workspace: `openspec/changes/vita-asistente-integral/CONTRATOS-VITA.md`.
Respuesta de coordinación: `RESPUESTA-AI-CONTRATO.md` del mismo change. Sin push,
despliegue, secretos nuevos ni cambios de configuración remota desde este carril.

## Activación y compatibilidad

`VITA_RUNTIME_ENABLED=false` y `VITA_VOICE_ENABLED=false` por defecto. El adaptador
también exige `VOICE_ENABLED` y `VITA_VOICE_TRANSPORT_VERIFIED`, una versión y agente
Custom LLM dedicados y secretos configurados. El modo remoto anterior permanece como
camino legado. No se modifica `docs/elevenlabs/agent-config.md` del carril raíz.

Texto exige además `ChatRequest.supports_vita=true` (BACK: `supportsVita`). Ausente o
false conserva el flujo previo y la configuración explícita ERP, incluso con flag
global activo; WhatsApp sigue legado. La capacidad no sustituye perfil, consentimiento
ni catálogo autorizado. Voz negocia por separado `supports_vita_voice=false` por defecto:
clientes antiguos conservan respuesta legada exacta. Cliente true en modo legacy
reserva vínculo durable y recibe session_id/thread_id/runtime_mode, sin session_token.

La identidad/política educativa de Vita se inyecta desde código en general,
especialistas, rondas de herramientas y voz. ERP, memoria, documentos e historial
son referencias de menor autoridad. Se mantienen controles de programa, idioma por
turno y orientación a SOS. No se ofrecen citas/CTA. `suggest_appointment` se filtra
tanto al enlazar como al ejecutar, incluso si una configuración antigua la enumera.
La verificación opcional de voz legada rechaza herramientas de citas adjuntas.

`tools=[]` en configuración ERP conserva exclusivamente este conjunto congelado:
`calculate`, `get_current_time`, `get_patient_data`, `mark_control_declined`,
`suggest_appointment` (esta última deshabilitada para paciente). Nuevos registros
no se heredan. `build_graph(tools=[])` sí significa ninguna herramienta. La misma
selección se utiliza para enlazar y ejecutar; llamadas desconocidas o fuera del
alcance no ejecutan nada. Las tools de perfil no amplían esa lista accidentalmente.

## Lecturas de la app

| Tool sin argumentos | Scope BACK |
| --- | --- |
| `get_my_day` | `day` |
| `get_my_nutrition` | `nutrition` |
| `get_my_assessments` | `assessments` |
| `get_my_podcast` | `podcast` |
| `get_my_exercise` | `exercise` |
| `get_my_progress` | `progress` |

GET `/api/v1/internal/vita/context?userId=&scope=` con clave interna. La identidad
sale de `configurable.user_id`, derivada del JWT por BACK. Argumentos de identidad,
fecha o paciente enviados por el modelo se rechazan antes de ejecutar. Sin identidad
no hay consulta. No se leen schemas de planes ni se calculan vigencia, nutrición o XP.

Se conservan `ok/empty/unavailable/forbidden/stale`, fecha local, revisión, datos,
acciones y procedencia del envelope BACK v1.1. Se exige coherencia HTTP: ok/empty
200, forbidden 403, unavailable 503, stale 409. Error arbitrario o envelope incoherente
no se acepta como datos/acciones. Stale descarta contenido previo; empty conserva
availability del workflow, incluido pending_review. 404 de una fachada inexistente es
`unavailable`, no prueba de ausencia de plan. Se rechazan acciones con URL/destino
arbitrario y respuestas mayores a 64 KB; los estados no `ok` no emiten acciones.
Timeout HTTP total por lectura: 3 s por defecto. No hay reintentos automáticos.

Las seis lecturas se conceden explícitamente en el runtime Vita **después** de
validar paciente/consentimiento mediante el catálogo BACK. También están disponibles
para un especialista autorizado sin exigir reconfigurar versiones ERP previas. Este
overlay de código no concede escrituras, no hereda `get_patient_data` grueso y no
interpreta metadata como permisos. La configuración ERP almacenada no se modifica.

Ante «marca mi rutina/podcast como completado» se consulta el contenido vigente y
se ofrece únicamente navegación. APP prepara y confirma con JWT y las APIs canónicas.
El prompt prohíbe afirmar completación/XP sin resultado registrado de BACK. Abrir,
escuchar o consultar nunca completa ni concede XP. Tests no se contestan ni envían.

## Catálogo, modelo y RAG

GET `/api/v1/internal/vita/agents?userId=` se consulta por turno. Se consume el array
BACK `id/runtimeKey/slug/name/description/specialty/versionId/versionNumber/routing`.
`routing` solo contiene `topics/keywords/channels`. Selección explícita por GUID/slug,
reglas del catálogo y continuidad breve. Canal vacío excluye el agente. Un agente
nuevo no requiere otro slug en código. El clasificador estructurado se usa ante
empate o consulta temática personal sin coincidencia literal; incluye descripción de
candidatos y valida el ID autorizado. Timeout/fallos degradan a general/aclaración.
Saludos compuestos ES/EN, identidad, agradecimientos, ayuda de app y cálculo simple
evitan el clasificador. La consulta «Últimamente me siento desbordado y me cuesta
dormir» permite elegir psicología sin añadir keywords o slugs fijos.

Se ejecuta la configuración/modelo/proveedor reales de la versión seleccionada.
Discrepancia catálogo/runtime falla cerrada. La caché revalida fila activa, versión,
configuración y alcance RAG cada turno. Se conserva precedencia GUID en la ruta
legada. No hay herramienta de transferencia recursiva: máximo un especialista/turno.

RAG conserva globales activas y bases propias autorizadas. Sin IDs no se consulta ni
se genera embedding. El SQL existente exige documento `Listo` y KB `Activo`; se
mantiene ese filtro. Cambios de configuración o KB recompilan. Resultados de tools
de turnos anteriores no se reintegran como evidencia vigente. Las fuentes del turno
se entregan en `sources`; no se copian PDFs a ElevenLabs. Las respuestas históricas
siguen siendo historial, no autoridad de una fuente todavía activa.

## Presupuestos y observabilidad

Para ERP (`AgentRuntimeConfig`), campos opcionales documentados para vita-erp:

| Campo | Default | Validación |
| --- | --- | --- |
| `max_tool_calls` | 8 | 0..16 |
| `tool_timeout_seconds` (nuevo) | 3 | >0..8 s |
| `recursion_limit` (existente) | 25 | 3..50 |

No se añaden campos al DTO de catálogo ni se exige editor de budgets para activar
las lecturas. El backend debe mantener un recursion_limit suficiente para las rondas
permitidas. Ruta general: máximo 3 llamadas. Lotes fuera de presupuesto se rechazan
sin efectos; una respuesta final acotada evita bucles de un modelo que insiste.
Las llamadas incompletas no se reenvían huérfanas en el siguiente turno.

`VITA_TURN_TIMEOUT=20` limita generación/herramientas, tanto sync como SSE. La
resolución previa tiene límites HTTP de 3 s y clasificador de 2 s. Cancelar propaga
cancelación al grafo/tools HTTP. La limpieza de voz se protege del CancelScope de
Starlette. Los logs nuevos contienen agente/versión/motivo/tiempos/estado; no tokens,
transcripciones o PHI. ExecutionTracker de Vita omite preguntas, respuestas y memorias
en trazas técnicas; el checkpoint mantiene el historial autorizado. No hay benchmark
p50/p95 de dispositivo ni se atribuye una causa al atasco de audio reportado.

## Adaptador de voz

POST `/internal/voice/session` mantiene los campos snake_case previos y añade en
modo Vita `thread_id/session_id/session_token/expires_at/runtime_mode`. BACK convierte
a camelCase. Si falta hilo se crea uno; el checkpoint siempre usa
`user_id::thread_id`. BACK conserva la autorización del hilo visible. Un ID ajeno
nunca abre el namespace del otro usuario.

Token opaco `v1.payload.signature`, HMAC-SHA256, audiencia `vita-custom-llm`, TTL máximo
600 s. Claims visibles: `sid/aud/exp`; sin usuario, paciente, JWT ni datos clínicos.
Identidad, hilo, conversación, expiración y revocación viven en `ai.vita_voice_sessions`.
Secretos dedicados: `VITA_VOICE_CONTEXT_SECRET` y `VITA_VOICE_SERVICE_KEY`, mínimo
32 bytes. Versión/agente: `VITA_ELEVENLABS_VERSION_ID`, `VITA_ELEVENLABS_AGENT_ID`.

DELETE `/internal/voice/session/{id}?user_id=` con clave interna: solo propietario,
idempotente 204, ajeno/inexistente 404. Se comprueba sesión durable cada 250 ms mientras
el runtime espera y consentimiento/catálogo cada segundo. Revocar cancela generación
y herramientas pendientes. No existe endpoint que importe respuestas arbitrarias de bot.

POST `/internal/voice/v1/chat/completions`: autenticación `Authorization: Bearer`
con clave de servicio dedicada más tres cabeceras:

| Cabecera | Dynamic variable |
| --- | --- |
| `X-Vita-Session` | `secret__vita_session_token` |
| `X-Vita-Conversation` | `system__conversation_id` |
| `X-Vita-Turn` | `system__agent_turns` |

Configurar **CustomLLM.url BASE terminada `/v1`**; el proveedor añade
`/chat/completions`. Usar `custom_llm.api_key: {"secret_id":"..."}`; un Authorization
literal en `request_headers` fue sustituido por el proveedor en el POC real.
`request_headers` usa objetos `{"variable_name":"..."}`. APP entrega únicamente el
token reducido como dynamic variable al iniciar; nunca el JWT del paciente.

El body compatible acepta `stream:true/messages`. Solo se procesa la última entrada
`user`; system/developer/assistant/tool del proveedor no sustituyen el historial ni
autorizan instrucciones. Se ignoran overrides de modelo/tools/KBs y se rechazan
identidades extra en el body. Contador del proveedor más ordinal/huella del prefijo
de entradas `user` ligan cada turno a la sesión. Replay completado recupera el
checkpoint sin generar otra vez; replay en vuelo/interrumpido o contenido cambiado
devuelve 409. Frases iguales con ordinales distintos son turnos distintos. Historial
recortado/reordenado se rechaza; no hay deduplicación por igualdad de texto.

Voz prepara en código la capacidad solicitada y RAG del especialista (si procede),
con las herramientas comunes. Después el nodo `answer`, sin herramientas enlazadas,
emite texto progresivo con una sola generación. El adaptador solo transmite ese nodo,
filtra bloques thinking/internos y produce SSE Chat Completions + `[DONE]`. Se probó
por ASGI que el primer contenido sale mientras la generación todavía está detenida.
El tiempo `first_token_ms` comienza al entrar al endpoint e incluye su resolución.
El nodo real también recibe una política de entrega de voz compartida con el builder:
turnos breves/naturales, una pregunta útil, fuentes habladas breves, unidades legibles,
sin markdown, UUIDs, URLs crudas, nombres de tools, repetir saludo o suplantar humano.
Se aplica igualmente al especialista ERP; chat conserva su formato y no se impone
otro max_tokens a modelos thinking. No añade generación.

`ai.vita_voice_turns` guarda recibos/huellas/estado, no otra copia del transcript.
Bloqueo `SELECT FOR UPDATE` de sesión y unicidad `(session_id, ordinal)`/contador;
advisory lock transaccional por hilo para texto/voz entre conexiones/réplicas. Todo
ello requiere PostgreSQL, no un dict de proceso. Migración nueva `fe62a09c1741`, solo
schema `ai`. No se aplicó a la BD compartida. Repositorios/conexiones reales y
upgrade/rollback se verificaron después en una BD aislada (evidencia abajo).

## Evidencia y límites de integración

Pruebas locales: `tests/test_vita_{contract,routing,runtime,safety,voice}.py`, regresiones
existentes de agentes, RAG, idiomas, voz, chat, hilos y controles. Modelos fake sin
keys, HTTP MockTransport y ASGI. Cubren identidad, estado/acciones, datos base y
especialista ES/EN, límites, cancelación, token manipulado/expirado, conversación
ajena, revocación en vuelo, cabeceras, replay y frases iguales distintas.

`tests/test_vita_postgres.py` es opt-in: exige `VITA_TEST_DATABASE_URL` de una DB local
aislada cuyo nombre contenga `test`. Crea/retira un schema temporal solo allí; prueba
dos repositorios/conexiones, revocación, recibos y advisory lock. Incluye aplicación y
rollback reales de la revisión de voz sobre baseline sintética aislada. Sin esa
variable quedan skipped; no se utiliza la BD compartida para completar estos checks.
Los SQL versionados son exportaciones offline; su ejecución aislada se acredita abajo.

Contrato local reusable: `docs/contracts/vita-ai.openapi.json`, más SQL offline.
Reproducir: `uv run python scripts/export_vita_contract.py`. No se inicializó/publicó
un workspace Postman; el ejecutable disponible era el launcher de Windows, sin CLI
operativo. La evidencia upstream usa el transporte simulado autorizado por la misión.

El POC real del coordinador (`POC-TRANSPORTE-VOZ.md`) acredita exclusivamente dos
turnos textuales iguales con token/conversación correctos, contador/user_count 1→2,
autenticación dedicada y SSE. No acredita seguridad del runtime, audio móvil,
reintentos, recorte, interrupción o correlación de IDs. El POC adicional de IDs fue
rechazado por aprobación automática al abrir el túnel y no se sorteó.

El historial añade `message_id/channel/status/revision`. Voz usa `provider_input`
para entrada recibida y `generated` para respuesta generada: **no son prueba de audio
heard/final ni de correspondencia con event_id/response_id del SDK**. APP no debe
fusionar esos IDs por inferencia ni volver a enviar la transcripción como otra pregunta.
El historial final usa el transcript autenticado del proveedor, descrito abajo;
no necesita igualdad de chunk.id/response_id. La integración móvil, seguridad clínica
y latencia de dispositivo siguen pendientes. Custom LLM permanece apagado.

## Historial final de voz y feedback

`VITA_TRANSCRIPT_SYNC_ENABLED=false`, timeout total 5 s (máximo configurable 10 s).
BACK solicita `GET /api/v1/threads/{thread_id}/state?user_id=<JWT>&sync_voice=true`
solo con cliente compatible y consentimiento vigente chat + voz. Es una autorización
interna, no un parámetro del modelo; AI revalida catálogo/paciente/chat y propiedad.
Sin ese opt-in no consulta al proveedor. Solo sesiones cerradas/revocadas o expiradas,
máximo tres por GET, sin worker. Processing, proveedor caído o lock ocupado son
reintentables mediante voice_sync_status; no se guardan parciales ni se borra chat.

El vínculo durable guarda user/thread/conversation/agent/version/runtime_mode. El GET
oficial del proveedor debe devolver conversation_id y agent_id esperados y status done.
Solo role user/agent con message visible; se ignoran reasoning, tools, identidad del
body y original_message completo. IDs incluyen conversación + rol + transcript.id,
o source_event_id + ocurrencia, o índice absoluto si API antigua. Nunca igualdad de
texto ni equivalencia del source_event_id con un ordinal runtime.

Bajo el mismo lock del hilo se reemplaza únicamente el grupo de sesión, conservando
otros mensajes. Replay no duplica; corrección incrementa revision. No genera respuestas,
tools o memoria. Metadata: source=elevenlabs_transcript, voice_session_id,
provider_message_id nullable, source_event_id y status provider_final|interrupted.
Estos estados no afirman audio oído. El POC real de root documenta IDs null, eventos
2 y 14 no contiguos y PCM16k interrumpido: message106 frente a original_message135,
interrupted=true. La importación conserva message y flag. No acredita teléfono.

Chat sync y stream propagan el ID del ExecutionTracker existente al mensaje final
mediante configurable, incluyendo rechazo de guardrail. GET proyecta execution_id
nullable (BACK/APP executionId), sin consultas por fila ni inferencia por texto/orden.
Voz importada deja null. Reload preserva el ID real para feedback.

La revisión fe62a09c1741 aún no desplegada incluye ahora vínculo/sincronización e
índice owner/thread/created; usar la exportación de este lote al integrar.

## Cierre de validación local (14-11-2026)

- `uv sync --locked`: dependencias del lock instaladas, sin actualizar `uv.lock`.
- `uv run ruff check .`: correcto.
- `uv run pytest -q`: **675 passed, 2 skipped**, 40.31 s. Los skips corresponden a
  los dos tests PostgreSQL opt-in, ejecutados aparte con éxito. 32 warnings existentes: deprecación Starlette/
  httpx y `FakeDbSession.add` asíncrono del doble legado; no fallos de producto.
- `uv run pytest tests/test_vita_voice.py -k first_final_token -q`: 1 passed;
  primer contenido HTTP ASGI antes de liberar el final de la generación sintética.
- `uv run python scripts/export_vita_contract.py`: OpenAPI y upgrade/rollback SQL
  offline generados correctamente. Ninguna migración ejecutada sobre BD compartida.
- `git diff --check`: correcto antes de commit. Sin API keys LLM, llamadas LLM
  de producción, datos clínicos reales, push ni activación remota desde este carril.

Root integrará/pusheará a dev según la autorización de Luis, verificará CI y
despliegues. Este carril entrega únicamente commits locales y handoff.

## PostgreSQL real autorizado para el cierre

Root/BACK habilitó un cluster temporal PostgreSQL 18.6, loopback puerto 55437,
separado de la BD compartida. El wrapper de BACK
`scripts/vita/Invoke-VitaAiPostgresTests.ps1 -AiWorktree <vita-ai>` comprueba datadir,
crea una DB con UUID exclusivo y la elimina al terminar, sin imprimir su URI.

El primer intento de root falló por `ProactorEventLoop` en Windows, antes de las
aserciones. pytest-asyncio 1.4.0 usa Runner/loop_factory y no basta cambiar la policy
del proceso. Hook de tests `pytest_asyncio_loop_factories`: Selector solo para
`test_vita_postgres.py` en Windows; los demás conservan `asyncio.new_event_loop`.
No se modificó runtime ni configuración Linux ni se relajaron aserciones.

Última ejecución real con vínculo/sync y migración revisada: **2 passed, 3 warnings,
1.59 s**. Acredita competencia
entre dos repositorios/conexiones, un recibo para el mismo turno, replay sin nueva
generación, siguiente ordinal, rechazo de propietario ajeno, revocación compartida,
advisory lock/liberación y migración `fe62a09c1741` + rollback reales en DB descartable.
Tres warnings de Alembic por `path_separator` legado. El wrapper confirmó limpieza.
Log local protegido: `%TEMP%/vita-ai-postgres-efac7691d3c24df2b698d5e8b39b7700.log`.
No acredita despliegue en varias réplicas de producción ni audio móvil.

En la revisión final se corrigió además la consulta de embeddings de
`PgVectorRetriever`: usa `aembed_query` del SDK OpenAI existente. El loop ya no queda
bloqueado por la variante síncrona durante RAG. Providers síncronos inyectados de
compatibilidad se ejecutan en thread; los providers propios tienen método async.
Regresión local: cancelación llega al proveedor async y no se inicia consulta SQL
ni se utiliza un resultado tardío. No se llamaron APIs reales de embeddings.
# Incidente de conexión — 14-11-2026

ElevenLabs US devuelve en producción un JSON con `signed_url` únicamente; con
`include_conversation_id=true`, la conversación reservada está en el parámetro
`conversation_id` de ese URL. El cliente anterior solo leía el campo del JSON:
el enlace durable de clientes opt-in rechazaba la sesión con 502 antes del SDK.
CloudWatch mostraba emisión correcta seguida de 502 en `/internal/voice/session`.

El cliente conserva la credencial original y extrae el ID de la respuesta autenticada
del proveedor, aceptando también el campo JSON anterior. Si ambas fuentes existen
deben coincidir; valores vacíos, repetidos, inválidos o de más de 128 caracteres se
rechazan sin registrar URL/firma. No se acepta un ID aportado por el paciente.
No cambia el endpoint, los consentimientos, las claves ni los modos de runtime.

La regresión reproduce el recorrido HTTP completo de sesión legacy vinculada con
el JSON real de un solo campo; antes devolvía 502 y después devuelve 200 con vínculo.
Una prueba sintética real con el cliente corregido verificó que el ID reservado
coincide con el evento WebSocket, recibió audio y dos respuestas. Se eliminó esa
conversación. Una muestra: firma 1628 ms, primer texto 525 ms y primer audio 670 ms
desde apertura de WebSocket; no representan latencia de micrófono/playback en iPhone
ni percentiles. Configuración compartida del agente sin modificaciones.
