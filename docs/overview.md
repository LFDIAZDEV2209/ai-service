# Overview general — CoppAddresd AI Service

> Documento de explicación general: sirve para entender el servicio de principio a fin
> sin leer código. Complementa (y actualiza) `docs/architecture.md`, que refleja la
> Fase 1 histórica. Detalles por módulo: `docs/memory.md`, `docs/adaptive-memory.md`,
> `docs/observability.md`, `docs/agents-config.md`, `docs/elevenlabs/`.

## 1. ¿Qué es?

Un microservicio **Python 3.11+ / FastAPI / LangGraph** (puerto 8000) que actúa como
el "cerebro de IA" de la plataforma CoppAddresd: recibe mensajes de usuarios, decide
con un LLM (Anthropic Claude u OpenAI GPT) cómo responder, ejecuta herramientas,
consulta conocimiento interno (RAG), recuerda al usuario entre conversaciones y
aprende de su propio desempeño.

**Qué NO hace** (separación de responsabilidades con el backend .NET):

- No valida autenticación ni permisos (eso lo hace el backend; el backend decide
  quién puede hablar con qué agente y le reenvía el mensaje).
- No guarda datos de negocio (pacientes, citas, CRM). Solo su propio esquema `ai.`
  en el Postgres compartido.
- No expone secrets ni lógica del ERP.

Cadena de una petición: **App móvil / ERP → Gateway :5080 → Backend .NET → AI Service :8000**
(los clientes nunca hablan directo con el AI Service; el backend inyecta identidad y
contexto). El canal backend → AI usa el header `X-Internal-Key`.

## 2. Estructura del código

```
ai-service/
├── app/
│   ├── api/               # FastAPI: routes (chat, threads, ingest, internal, voice...)
│   │                      #   + deps (get_graph, get_db_session) + security (X-Internal-Key)
│   ├── graph/             # NÚCLEO LangGraph: state, nodes, graph, subgraphs/
│   ├── agents/            # Config de agentes: runtime_config (tipada), runtime_registry
│   │                      #   (compila/cachea grafos), prompts, registry de perfiles
│   ├── orchestration/     # Router de intenciones (stub, preparado para el futuro)
│   ├── tools/             # Tools del agente: builtin, appointment, control, retrieval, registry
│   ├── memory/            # Checkpointer LangGraph + memoria usuario + memoria adaptativa
│   ├── rag/               # chunker → embeddings → pgvector → retriever → ingest
│   ├── llm/               # Fábrica multi-proveedor (Anthropic/OpenAI) + costos
│   ├── safety/            # Guardrails: sanitización, prompt injection, rate limit
│   ├── observability/     # ExecutionTracker (best-effort): registro de ejecuciones
│   ├── voice/             # Integración ElevenLabs (voz, /internal/voice/session)
│   ├── db/                # SQLAlchemy async: modelos del schema `ai.`
│   └── core/              # config (pydantic-settings + .env), logging, errors
├── alembic/               # Migraciones SOLO del schema `ai.`
├── tests/                 # pytest sin API keys (FakeToolAwareModel, HashEmbeddings)
└── main.py / run_dev.py   # run_dev.py es OBLIGATORIO en Windows (SelectorEventLoop)
```

## 3. El grafo LangGraph, paso a paso

El agente es un **grafo de nodos** con un **estado compartido** (`AgentState`).
Cada nodo es una función async que recibe el estado, hace su trabajo y devuelve un
diccionario con los campos que quiere actualizar; LangGraph los fusiona con
**reducers** (p. ej. `messages` usa `add_messages`: agrega y deduplica por id).

### Flujo básico (agente sin memoria)

```
START → guardrails → agent ⇄ tools → END
          │            (bind_tools + ToolNode)
          └─ inseguro → END (rechazo, sin gastar tokens del LLM)
```

### Flujo completo (agentes sincronizados del backend, con memoria habilitada)

```
START → guardrails → memory_load → experience_load → agent ⇄ tools → memory_save → END
```

Qué hace cada nodo:

| Nodo              | Qué hace                                                                                                                                                                                              | Fuente                                           |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| `guardrails`      | Sanitiza el input (control chars, longitud máx.) y detecta prompt injection (patrones EN/ES). Si es inseguro → END con mensaje de rechazo.                                                            | `app/safety/guardrails.py`, `app/graph/nodes.py` |
| `memory_load`     | Carga la **memoria de largo plazo del usuario** y la deja en `state["memory_context"]`. Si no hay `user_id`, no hace nada.                                                                            | `make_memory_load_node`                          |
| `experience_load` | Carga los **patrones exitosos del propio agente** (adaptive memory) en `state["experience_context"]`.                                                                                                 | `make_experience_load_node`                      |
| `agent`           | Construye el prompt (system + bloques de contexto + historial + mensaje) y llama al LLM con las tools enlazadas (`bind_tools`). Si el LLM pide tools → va a `tools`; si responde → `memory_save`/END. | `make_agent_node`                                |
| `tools`           | Ejecuta las tools pedidas (ToolNode) y vuelve a `agent` (loop hasta que el LLM responda sin pedir más tools). Además acumula `tools_used` y `suggestions`.                                            | `make_tools_node`                                |
| `memory_save`     | Extrae hechos declarativos del mensaje del usuario, los persiste, y rota el **resumen rodante** si el historial creció. Nunca falla el turno por errores de BD.                                       | `make_memory_save_node`                          |

### El estado (`AgentState`, `app/graph/state.py`)

- `input`: mensaje del usuario (sanitizado tras guardrails).
- `messages`: historial completo con reducer `add_messages` (la clave del multi-turno).
- `guardrail`: resultado de validación (auditoría).
- `tools_used` / `suggestions`: acumuladores (`operator.add`).
- `rag_sources`: fuentes RAG usadas (auditoría).
- `memory_context` / `experience_context`: bloques de contexto inyectados al prompt.
- `provider` / `agent`: telemetría.

## 4. Gestión de contexto — el corazón del sistema

El contexto no vive en UN lugar: son **seis capas** con duración y aislamiento
distintos. Esta es la pregunta clave del diseño:

| #   | Capa                       | Dónde vive                                                      | Cuánto dura                          | Aislado por                 |
| --- | -------------------------- | --------------------------------------------------------------- | ------------------------------------ | --------------------------- |
| 1   | Conversación activa        | Checkpointer LangGraph (tablas `checkpoints*` del schema `ai.`) | Mientras exista el thread            | `thread_id`                 |
| 2   | Memoria del usuario        | `ai.agent_memories` (Postgres)                                  | Para siempre (con tope 500/usuario)  | `user_id` + `agent_type_id` |
| 3   | Resumen rodante            | `ai.agent_memories` (categoría `resumen`)                       | Se reescribe al crecer el historial  | `user_id` + `agent_type_id` |
| 4   | Experiencia del agente     | `ai.agent_experiences`                                          | Persistente, se refuerza con el uso  | `agent_type_id`             |
| 5   | Conocimiento (RAG)         | `ai.knowledge_chunks` (pgvector)                                | Persistente (indexado de documentos) | `knowledge_base_ids`        |
| 6   | Contexto efímero del turno | `configurable` (solo en memoria del request)                    | UN turno — nunca persiste            | request actual              |

### 4.1 Contexto de conversación (corto plazo) — checkpointer

LangGraph guarda automáticamente el estado completo del grafo al final de cada
turno con un **checkpointer**:

- **Dev/tests**: `MemorySaver` (en memoria del proceso).
- **Producción**: `AsyncPostgresSaver` sobre el Postgres compartido, schema `ai.`
  (construido con psycopg y `search_path=ai,public`). Es **async obligatorio**
  (`await get_checkpointer()`) porque el connection pool necesita un loop abierto.

El `thread_id` va en `config["configurable"]` de cada invocación:

```python
graph.ainvoke(
    {"input": mensaje, "agent": agent_type_id},
    config={"configurable": {"thread_id": ..., "user_id": ..., "patient_id": ...,
                             "agent_instance_id": ...}, "recursion_limit": 25},
)
```

Al volver con el mismo `thread_id`, el nodo `agent` recibe el historial previo en
`state["messages"]` (reducer `add_messages`) y construye el prompt con:
`[SystemMessage(s) + historial + HumanMessage(nuevo input)]`.

**Detalle importante**: el `thread_id` de almacenamiento se compone con el
`user_id` (`_storage_thread_id`), y el `configurable` también lleva `user_id`,
`patient_id` y `agent_instance_id` — el aislamiento no depende solo del thread.

### 4.2 Memoria del usuario (largo plazo) — `app/memory/`

- **Escritura** (`memory_save`): `extract_facts` (heurística determinista, sin LLM)
  detecta frases declarativas — "me llamo X", "soy alérgico a Y", "tengo diabetes",
  "prefiero Z", "quiero W" — y las guarda como `AgentMemory` con categoría
  (`personal`/`preferencia`/`clinico`/`objetivo`) e importancia (lo clínico pesa más).
  Deduplicación por contenido normalizado.
- **Resumen rodante**: a partir de 6 mensajes en el thread, `maybe_roll_summary`
  genera/reescribe un resumen acumulado (≤600 chars) — con el LLM del agente si hay
  `summarizer` inyectado, o con fallback determinista. Evita que el prompt crezca
  infinito: el historial crudo envejece, el resumen mantiene lo esencial.
- **Lectura** (`memory_load`): hasta 15 memorias formateadas como bloque de texto
  `Memoria del usuario:\n[categoria] hecho`, inyectado como SystemMessage adicional.
- **Regla de oro — aislamiento**: toda query filtra SIEMPRE por `user_id` +
  `agent_type_id` (y opcionalmente `agent_instance_id`). La memoria del agente de
  psicología de un paciente NO es visible para su agente de nutrición, ni para otro
  paciente. Cubierto por tests (`test_aislamiento_entre_usuarios`,
  `test_aislamiento_entre_agentes_del_mismo_usuario`).
- **Degradación con gracia**: si la BD falla, el chat sigue sin memoria (los errores
  se registran, nunca tumban el turno).
- **Gotcha de transacciones**: `extract_and_save` / `maybe_roll_summary` dejan la
  transacción abierta a propósito ("commit by caller"); el nodo DEBE llamar
  `service.commit()` o las escrituras se descartan en silencio.

### 4.3 Experiencia adaptativa del agente — `app/memory/adaptive.py`

Es memoria del **agente**, no del usuario (por eso viaja en un bloque separado):

1. El usuario califica una respuesta (1-5) → `POST /chat/feedback` → `AgentFeedback`.
2. Si el feedback trae `agent_type_id` + `trigger` + `response` (lo envía el backend
   con el contexto de qué se respondió), se convierte en **experiencia**:
   upsert por `trigger` — si ya existía, sube `recurrence_count` y promedia
   `success_rating`. Outcome `success` si rating ≥ 4.
3. En turnos futuros, `experience_load` inyecta las experiencias **exitosas y
   recurrentes** (ordenadas por `recurrence × rating`) como bloque
   `"Experiencia aprendida del agente (patrones que funcionaron)"`.
4. Además hay una **evaluación heurística** automática post-turno
   (`evaluate_response`: no vacía, no repite el prompt, longitud razonable, usó
   tools) que persiste `AgentEvaluation` — base para LLM-as-judge a futuro.

Es decir: el agente "aprende" qué respuestas funcionan ante qué disparadores, sin
fine-tuning — con contexto inyectado.

### 4.4 Conocimiento interno (RAG) — `app/rag/` + `app/tools/retrieval.py`

- **Ingesta**: el backend sube un documento (blob base64) a
  `POST /internal/agents/ingest` → extracción de texto → `chunk_markdown`
  (chunks por encabezados, contexto estructural preservado) → embeddings
  (OpenAI `text-embedding-3-small`, 1536 dims) → `ai.knowledge_chunks` (pgvector,
  índice HNSW).
- **Recuperación**: la tool `retrieve_knowledge(query)` (creada **por agente** con
  `make_retrieve_tool(knowledge_base_ids, top_k)`) busca semánticamente y devuelve
  un bloque citable `[Fuente: ... — Sección: ...]` + JSON de fuentes para el
  playground/observabilidad.
- **Aislamiento por KB (crítico)**: la tool solo se enlaza si
  `retrieval.enabled` **Y** `knowledge_base_ids` NO está vacío. Un agente nunca
  recupera chunks de KBs de otros agentes.
- Dev sin API key: `HashEmbeddingsProvider` determinista (misma dimensión 1536).

### 4.5 Contexto efímero por turno — `configurable`

Datos que guían UN turno pero **nunca se persisten** en el checkpointer:

- `control_context` (UC-001 "Controles"): el backend envía día de hito, estado del
  control y si falta examen; el nodo `agent` lo convierte en guía del prompt con
  `build_control_guidance`. Si el modelo decide rechazar el control, llama la tool
  no-op `mark_control_declined` → el endpoint emite `control_signal: declined`
  (evento propio en el stream, campo en la respuesta completa). Sin contexto, no se
  emite nada.
- Identidad: `user_id`, `patient_id`, `agent_instance_id` (leídos por los nodos de
  memoria para aislamiento).

La distinción state vs configurable es deliberada: **el estado persiste (historial,
contextos inyectados), el configurable no (guías de un solo turno, identidad)**.

### 4.6 ¿Y "entre agentes"? Cómo conviven varios agentes

No hay un mega-agente ni handoffs estilo swarm: hay **un grafo compilado por
tipo de agente**, y el aislamiento es estructural:

1. En el ERP el equipo crea un **tipo de agente** y versiona su configuración
   (prompt, modelo, temperatura, tools, RAG, memoria) — el JSON lo interpreta
   `AgentRuntimeConfig` (`app/agents/runtime_config.py`).
2. Al **activar una versión**, el backend la sincroniza en `ai.agent_runtime_configs`
   vía `POST /internal/agents/sync-config` → invalida el cache del
   `AgentRuntimeRegistry` (`app/agents/runtime_registry.py`).
3. En el primer chat, el registry **compila un grafo dedicado**: modelo con los
   overrides del agente (fábrica `get_chat_model`), subconjunto de tools,
   `retrieve_knowledge` limitado a sus KBs, nodos de memoria según
   `memory_config.enabled`, y su checkpointer. Se cachea (tope 128 grafos,
   thread-safe).
4. Cada ejecución viaja con su propio `configurable` (thread, user, patient,
   instancia) → historial, memorias y conocimiento quedan separados **por
   construcción**, no por disciplina del LLM.

También existe el patrón **subgrafo con estado aislado** (`app/graph/subgraphs/rag_agent.py`):
un sub-agente con su propio esquema de estado (`RagState`) que se invoca dentro de
un nodo del supervisor, mapeando `padre → hijo` y devolviendo solo el resultado
sintetizado — el sub-agente no contamina el historial del supervisor. Hoy es el
patrón de referencia (el RAG real opera como tool, no como subgrafo conectado).

## 5. Streaming en vivo (SSE)

`POST /api/v1/chat/stream` con `stream_mode=["updates","messages","values","tasks"]`:

- `token`: texto del AIMessage chunk a chunk (con el nodo que lo emite → el front
  marca el nodo activo).
- `node`: cada nodo que termina (updates).
- `flow`: inicio/fin de cada nodo con `step`, `ts`, `duration_ms` (del modo `tasks`)
  — alimenta la visualización de flujos del ERP; la traza completa queda en
  `output.flow_trace` de la ejecución.
- `done` / `control_signal` / `error`.

## 6. Observabilidad (fase 7)

Cada turno registra una `AgentExecution` (`app/observability/executions.py`,
best-effort: nunca bloquea el chat): estado, latencia, tokens, modelo, input/output,
`flow_trace`. Se vincula con feedback (`AgentFeedback`), evaluaciones
(`AgentEvaluation`) y experiencias (`AgentExperience`). Consulta:

- `GET /api/v1/admin/executions` (filtros: agente, usuario, status, fechas, paginación).
- `GET /api/v1/admin/executions/{id}` — detalle que responde las 12 preguntas del
  monitoreo (¿respondió?, ¿tardó?, ¿cuánto costó?, ¿el usuario quedó conforme?...).

## 7. Voz (ElevenLabs)

`app/voice/` + `POST /internal/voice/session`: el backend pide una sesión de voz
(config del agente para ElevenLabs) para las llamadas/conversaciones por voz de la
app móvil. Detalles en `docs/elevenlabs/`.

## 8. Endpoints

Públicos (`/api/v1`):

```
POST /chat              # turno completo → ChatResponse (answer, tools_used, execution_id...)
POST /chat/stream       # SSE: tokens + nodos + flow + done
POST /chat/feedback     # rating 1-5 → feedback (+ experiencia adaptativa)
GET  /threads           # conversaciones
GET  /admin/executions  # listado de ejecuciones (observabilidad)
GET  /admin/executions/{id}
GET  /health
```

Internos (backend → AI, `X-Internal-Key`, sin prefix):

```
POST /internal/agents/sync-config       # activa versión → invalida cache del registry
GET  /internal/agents/{id}/graph        # descriptor del grafo (nodos/aristas + config efectiva)
POST /internal/agents/ingest            # indexa documento (base64) → pgvector
DELETE /internal/agents/ingest/{id}     # elimina chunks de un documento
POST /internal/voice/session            # sesión de voz ElevenLabs
```

## 9. Cómo correrlo

```bash
cd ai-service
uv sync                                  # dependencias
uv run python run_dev.py                 # dev server :8000 (SIEMPRE esto en Windows)
uv run pytest                            # tests (sin API keys ni BD real)
uv run ruff check .                      # lint
uv run alembic upgrade head              # migraciones (solo schema ai.)
docker compose up -d                     # desde la RAÍZ del workspace: Postgres+pgvector y Valkey
```

- `.env` requerido (copiar de `.env.example`): `ANTHROPIC_API_KEY` o `OPENAI_API_KEY`,
  `DATABASE_URL` (Postgres del compose raíz), `INTERNAL_API_KEY`.
- Windows: nunca `uvicorn main:app` directo (ProactorEventLoop rompe psycopg async;
  `run_dev.py` fuerza `SelectorEventLoop`).
- Un solo Postgres compartido con el backend: schema `ai.` para TODO lo de este
  servicio (incluidas las tablas del checkpointer). Alembic filtrado con
  `include_object` — jamás toca schemas del backend.

## 10. Tests

Sin API keys ni Postgres: `FakeToolAwareModel` (LLM falso que decide tool calls),
`HashEmbeddingsProvider`, `MemorySaver` como checkpointer factory, y sesiones
SQLite/in-memory o fakes para memoria. Suites: `test_graph`, `test_agent_graph`,
`test_memory`, `test_adaptive_memory`, `test_guardrails`, `test_rag`, `test_api`,
`test_runtime_registry`, `test_observability`, `test_voice`, `test_lab_exam`,
`test_proactive`, `test_plan_generation`, `test_appointment_suggestion`, etc.

```bash
uv run pytest tests/test_memory.py -v
uv run pytest tests/test_graph.py -k "test_name"
```
