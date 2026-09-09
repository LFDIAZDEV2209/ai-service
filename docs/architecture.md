# Arquitectura — CoppAddresd AI Service

## Objetivo del servicio

CoppAddresd AI Service es un servicio independiente de agente de IA que:
- Recibe mensajes de usuarios
- Procesa con un LLM (Anthropic Claude o OpenAI GPT)
- Devuelve respuestas estructuradas
- Maneja memoria de conversación (multi-turno)
- Ejecuta herramientas seguras (calculadora, hora actual)
- Valida seguridad (guardrails contra prompt injection)

**Responsabilidad:** Ser el cerebro de IA de la plataforma CoppAddresd. No maneja lógica de negocio, autenticación, ni base de datos de usuarios (eso lo hace el backend .NET).

---

## Arquitectura actual (Fase 1)

```
┌─────────────────────────────────────────────────────────────┐
│                         API Layer                            │
│  FastAPI + Uvicorn (puerto 8000)                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ /chat    │  │ /stream  │  │ /threads │  │ /health  │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    Agent Runtime (LangGraph)                 │
│                                                              │
│  START → guardrails → agent ⇄ tools → END                   │
│            │                                                 │
│            └─ inseguro → END (rechazo)                      │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  AgentState (estado compartido)                      │  │
│  │  - input: mensaje del usuario                        │  │
│  │  - messages: historial (con reducer add_messages)    │  │
│  │  - guardrail: resultado de validación                │  │
│  │  - tools_used: herramientas ejecutadas               │  │
│  │  - rag_sources: fuentes RAG usadas                   │  │
│  │  - provider: modelo LLM usado                        │  │
│  │  - agent: perfil de agente activo                    │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  Checkpointer (MemorySaver dev / PostgresSaver prod)        │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    LLM Provider Factory                      │
│  ┌──────────────────┐  ┌──────────────────┐                │
│  │ Anthropic        │  │ OpenAI           │                │
│  │ (claude-sonnet)  │  │ (gpt-4o)         │                │
│  └──────────────────┘  └──────────────────┘                │
│  Configurado por .env (LLM_PROVIDER, *_API_KEY, *_MODEL)   │
└─────────────────────────────────────────────────────────────┘
```

---

## Componentes y responsabilidades

### `app/api/` — Capa HTTP
- **`main.py`**: `create_app()` — crea la app FastAPI, registra routers, configura CORS
- **`deps.py`**: `get_graph()` — dependencia que devuelve el grafo compilado (cacheado)
- **`schemas.py`**: Modelos Pydantic (ChatRequest, ChatResponse, etc.)
- **`routes/chat.py`**: `POST /chat` (respuesta completa) + `POST /chat/stream` (SSE)
- **`routes/health.py`**: `GET /health` — healthcheck
- **`routes/threads.py`**: `GET /threads/{id}/state` — historial de conversación
- **`routes/ingest.py`**: `POST /ingest` — indexar documentos para RAG

### `app/graph/` — Núcleo del agente (LangGraph)
- **`state.py`**: `AgentState` — TypedDict con reducers (mensajes se acumulan, tools se rastrean)
- **`nodes.py`**: Tres nodos:
  - `guardrails_node`: valida y sanitiza input
  - `agent_node`: invoca LLM con tools enlazadas
  - `tools_node`: ejecuta herramientas pedidas por el LLM
- **`graph.py`**: `build_graph()` — ensambla y compila el grafo supervisor
- **`subgraphs/rag_agent.py`**: Subgrafo RAG (referencia, no conectado aún)

### `app/agents/` — Perfiles de agentes
- **`prompts.py`**: `BASE_SYSTEM_PROMPT` — prompt de sistema del agente base
- **`registry.py`**: `AgentProfile` + `AGENTS` dict — registro de perfiles (solo "base" activo)

### `app/llm/` — Fábrica de modelos LLM
- **`factory.py`**: `get_chat_model()` — crea modelo según proveedor (Anthropic/OpenAI)
- **`cost.py`**: `SessionStats` — tracking de tokens y estimación de costos USD

### `app/memory/` — Persistencia
- **`checkpointer.py`**: `get_checkpointer()` — MemorySaver (dev) o PostgresSaver (prod)
- **`store.py`**: `JsonFileMemoryStore` — memoria de largo plazo (entre threads)

### `app/safety/` — Seguridad
- **`guardrails.py`**: 
  - `sanitize_input()`: limpia control chars, limita longitud
  - `detect_prompt_injection()`: detecta patrones EN/ES
  - `RateLimiter`: ventana deslizante por IP
  - `check_input_guardrails()`: punto de entrada del grafo

### `app/tools/` — Herramientas del agente
- **`registry.py`**: `ALL_TOOLS` — recolecta tools decoradas con `@tool`
- **`builtin.py`**: Tools genéricas:
  - `get_current_time(timezone)`: hora actual en zona IANA
  - `calculate(expression)`: evalúa expresión aritmética segura

### `app/rag/` — Pipeline RAG (completo, no conectado al agente aún)
- **`chunker.py`**: Divide markdown por encabezados
- **`embeddings.py`**: `OpenAIEmbeddingsProvider` + `HashEmbeddingsProvider` (dev)
- **`vector_store.py`**: `InMemoryVectorStore` (coseno)
- **`retriever.py`**: Query → top-k chunks
- **`service.py`**: Singleton retriever
- **`ingest.py`**: Directorio → chunks → embeddings → vector store

### `app/orchestration/` — Enrutamiento (stub)
- **`router.py`**: `IntentRouter` — siempre retorna "base" (preparado para futuro)

### `app/core/` — Configuración y utilidades
- **`config.py`**: `Settings` (pydantic-settings) — lee `.env`
- **`errors.py`**: Jerarquía de excepciones (`CoppAiError`, `ConfigError`, etc.)
- **`logging.py`**: `setup_logging()` + `get_logger()`

---

## Flujo de una petición

### 1. Request llega a FastAPI
```
POST /api/v1/chat
{
  "message": "¿Qué hora es en México?",
  "thread_id": "abc123",  // opcional
  "agent": "base"
}
```

### 2. Rate limiting por IP
- `chat.py:_check_rate_limit()` valida que no exceda 10 req/min por IP
- Si excede → 429 Too Many Requests

### 3. Grafo se invoca
```python
result = await graph.ainvoke(
    {"input": message, "agent": agent},
    config={"configurable": {"thread_id": thread_id}, "recursion_limit": 25},
)
```

### 4. Nodo guardrails
- Sanitiza input (elimina control chars, limita longitud)
- Detecta prompt injection (EN/ES)
- Si es inseguro → retorna AIMessage de rechazo, grafo termina en END
- Si es seguro → continúa al agent_node con input sanitizado

### 5. Nodo agent
- Construye prompt: `[SystemMessage, *history, HumanMessage(input)]`
- Invoca LLM con tools enlazadas (`model.bind_tools(ALL_TOOLS)`)
- LLM decide si responder directo o llamar tools
- Si llama tools → retorna AIMessage con `tool_calls`, grafo va a tools_node
- Si responde directo → retorna AIMessage, grafo va a END

### 6. Nodo tools (si aplica)
- Ejecuta herramientas pedidas por el LLM
- Regresa ToolMessage con resultados
- Grafo vuelve a agent_node (loop)

### 7. Checkpointer persiste estado
- MemorySaver (dev) o PostgresSaver (prod) guarda el estado
- Próximo request con mismo `thread_id` recupera historial

### 8. Respuesta al cliente
```json
{
  "thread_id": "abc123",
  "answer": "La hora en México es 14:30:45",
  "agent": "base",
  "tools_used": ["get_current_time"],
  "model": "ChatAnthropic"
}
```

---

## Configuración

### Variables de entorno (`.env`)

```bash
# App
ENVIRONMENT=development          # development | production
LOG_LEVEL=INFO
API_PREFIX=/api/v1

# LLM
LLM_PROVIDER=anthropic           # anthropic | openai
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-6
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o
OPENAI_EMBEDDING_MODEL=text-embedding-3-small

# Agente
MAX_TOOL_CALLS=8
RECURSION_LIMIT=25
LLM_TEMPERATURE=0.2
LLM_MAX_TOKENS=4096

# Seguridad
MAX_INPUT_LENGTH=8000
RATE_LIMIT_MAX_REQUESTS=10
RATE_LIMIT_WINDOW_SECONDS=60

# Memoria
DATABASE_URL=                    # vacío → MemorySaver; postgres://... → PostgresSaver
MEMORY_STORE_PATH=./data/memory.json

# RAG
DOCS_PATH=./docs
RAG_TOP_K=5
```

---

## Manejo de errores

### Jerarquía de excepciones (`app/core/errors.py`)
```
CoppAiError (base)
├── ConfigError          # Falta API key, proveedor inválido
├── GuardrailError       # Input/output bloqueado
├── ProviderError        # Error comunicando con LLM
├── ToolExecutionError   # Error ejecutando herramienta
└── MemoryStoreError     # Error en persistencia
```

### Manejo en la API
- `ConfigError` → 503 Service Unavailable (falta configuración)
- `CoppAiError` → 500 Internal Server Error
- Rate limit excedido → 429 Too Many Requests
- Thread no encontrado → 404 Not Found

---

## Testing

### Estrategia
- **22 tests** cubren grafo, API, guardrails, RAG
- **Sin API keys necesarias**: usan `FakeToolAwareModel` (LLM falso) y `HashEmbeddingsProvider` (embeddings deterministas)
- **Fixture `simple_graph`**: grafo con LLM falso para tests del grafo
- **Override de dependencias**: tests de API sobrescriben `get_graph()` con grafo falso

### Ejecutar tests
```bash
uv run pytest                    # todos los tests
uv run pytest -v                 # verbose
uv run pytest tests/test_graph.py::test_agent_answers_simple_message  # test específico
```

---

## Decisiones arquitectónicas

### ¿Por qué LangGraph?
- **Checkpointer nativo**: persistencia de estado multi-turno sin código custom
- **Streaming granular**: tokens + nodos en vivo vía SSE
- **Subgrafos**: permite aislar agentes especializados (RAG, doctor, etc.) sin contaminar estado del supervisor
- **Human-in-the-loop**: `interrupt` / `Command` para aprobaciones (futuro)

### ¿Por qué multi-proveedor (Anthropic + OpenAI)?
- **Flexibilidad**: cambiar de proveedor sin tocar lógica del grafo
- **Resiliencia**: fallback si un proveedor falla
- **Costos**: comparar precios según caso de uso

### ¿Por qué tools separadas de ejecución?
- **Registry pattern**: definición (`@tool`) separada de ejecución (`ToolNode`)
- **Auto-colección**: `ALL_TOOLS` recolecta tools automáticamente del módulo `builtin.py`
- **Seguridad**: tools validadas y limitadas (no `exec`, no acceso a red)

### ¿Por qué guardrails en nodo separado?
- **Separación de responsabilidades**: validación vs. generación
- **Rechazo temprano**: input inseguro no llega al LLM (ahorra tokens)
- **Auditoría**: `state["guardrail"]` registra qué se bloqueó y por qué

### ¿Por qué RAG no conectado aún?
- **Fase 1**: estabilizar agente base antes de agregar complejidad
- **Subgrafo aislado**: cuando se conecte, será un nodo que invoca subgrafo RAG con estado aislado
- **Preparado**: pipeline completo existe, solo falta conectar al supervisor

---

## Evolución futura

### Fase 2 — Lógica de negocio
- Conectar subgrafo RAG al supervisor (cuando haya documentación real)
- Agregar perfiles de agentes especializados (doctor, psychologist, nutritionist)
- Implementar `IntentRouter` para clasificar intención y enrutar a subgrafos

### Fase 3 — Integración con backend .NET
- Tools que llaman al backend (API REST) para datos de pacientes, citas, etc.
- Autenticación JWT en endpoints de la API
- Webhooks para notificaciones

### Fase 4 — Producción
- Migrar a PostgresSaver (checkpointer persistente)
- Migrar vector store a pgvector o Chroma
- Deploy con Docker + docker-compose
- Observabilidad (logs estructurados, métricas, traces)

### Fase 5 — Frontend + voz
- Frontend para crear/configurar agentes (perfiles en `AGENTS` registry)
- Integración con ElevenLabs para voz
- Streaming bidireccional (audio → texto → audio)

---

## Qué NO forma parte de esta fase

- ❌ Integración con backend .NET
- ❌ Autenticación / autorización / roles
- ❌ Base de datos de usuarios
- ❌ Lógica de negocio (pacientes, citas, CRM)
- ❌ Lógica específica de psicología, medicina, nutrición
- ❌ Frontend
- ❌ Voz / ElevenLabs
- ❌ RAG con documentación real (pipeline existe, no conectado)
- ❌ Multi-agent orchestration compleja
- ❌ Memoria avanzada (solo MemorySaver + JsonFileMemoryStore)
- ❌ Deployment de producción
- ❌ Infraestructura innecesaria

**Enfoque actual:** Agente base estable y probado, independiente del backend.
