# 📚 Guía de aprendizaje — CoppAddresd AI Service

> Documentación educativa **aparte del README**. El README te dice *qué* es el
> proyecto; esta guía te explica *cómo funciona por dentro* y **por qué** está
> hecho así, archivo por archivo
>
>
> Nivel: desarrollador con bases (sabes Python, sabes APIs)

---

## 📑 Tabla de contenidos

1. [Parte 1 — Los conceptos antes que el código](#parte-1--los-conceptos-antes-que-el-código)
   - [¿Qué es un LLM? ¿Qué es un agente?](#qué-es-un-llm-qué-es-un-agente)
   - [¿Qué es LangGraph? La máquina de estados](#qué-es-langgraph-la-máquina-de-estados)
   - [¿Qué es LangChain?](#qué-es-langchain)
   - [¿Y FastAPI?](#y-fastapi)
2. [Parte 2 — La arquitectura de un vistazo](#parte-2--la-arquitectura-de-un-vistazo)
   - [El flujo de una petición, paso a paso](#el-flujo-de-una-petición-paso-a-paso)
   - [Mapa general de archivos](#mapa-general-de-archivos)
3. [Parte 3 — Explicación archivo por archivo](#parte-3--explicación-archivo-por-archivo)
   - [Raíz del proyecto](#raíz-del-proyecto)
   - [`app/core` — config, logging, errores](#appcore--config-logging-errores)
   - [`app/llm` — la fábrica de modelos](#appllm--la-fábrica-de-modelos)
   - [`app/tools` — el registro de herramientas](#apptools--el-registro-de-herramientas)
   - [`app/safety` — los guardrails](#appsafety--los-guardrails)
   - [`app/rag` — el pipeline RAG](#apprag--el-pipeline-rag)
   - [`app/memory` — checkpointer y memoria](#appmemory--checkpointer-y-memoria)
   - [`app/graph` — el corazón LangGraph](#appgraph--el-corazón-langgraph)
   - [`app/agents` — prompts y perfiles](#appagents--prompts-y-perfiles)
   - [`app/orchestration` — el router](#apporchestration--el-router)
   - [`app/api` — FastAPI](#appapi--fastapi)
   - [`tests/` — cómo se prueba sin API keys](#tests--cómo-se-prueba-sin-api-keys)
4. [Parte 4 — Los conceptos de LangGraph con código real](#parte-4--los-conceptos-de-langgraph-con-código-real)
   - [El Estado y los reducers](#el-estado-y-los-reducers)
   - [El checkpointer y el `thread_id`](#el-checkpointer-y-el-thread_id)
   - [El loop de herramientas: `bind_tools` + `ToolNode`](#el-loop-de-herramientas-bind_tools--toolnode)
   - [Streaming: cómo llegan los tokens al frontend](#streaming-cómo-llegan-los-tokens-al-frontend)
   - [Subgraphs: agentes con estado aislado](#subgraphs-agentes-con-estado-aislado)
5. [Parte 5 — Recetas para extenderlo](#parte-5--recetas-para-extenderlo)
6. [Parte 6 — Glosario rápido](#parte-6--glosario-rápido)
7. [Parte 7 — Errores comunes y cómo depurar](#parte-7--errores-comunes-y-cómo-depurar)

---

# Parte 1 — Los conceptos antes que el código

## ¿Qué es un LLM? ¿Qué es un agente?

Un **LLM** (Large Language Model) es un modelo de texto gigante. Le das un
texto y te devuelve texto. Solo eso. No puede hacer nada más: **no sabe la
hora, no lee archivos, no consulta bases de datos**.

Un **agente** es: un LLM **+ herramientas** (funciones que puede pedir
ejecutar) **+ un bucle** que repite "el LLM pide → se ejecuta → se le devuelve
el resultado → el LLM decide". Eso es exactamente lo que hace el proyecto del
curso (`node-dev-assistant`): un `while` que llama a Claude, y si Claude
responde con `tool_use`, ejecuta la tool y vuelve a llamar. Ese es el famoso
**agentic loop**.

LangGraph hace ese bucle **de forma explícita, persistente y escalable** en
vez de un `while` escondido dentro de un objeto.

## ¿Qué es LangGraph? La máquina de estados

LangGraph modela al agente como una **máquina de estados**: hay un **Estado**
(un dict) que viaja por un **grafo** de **nodos** conectados por **aristas**.
Es como un flowchart que se ejecuta.

Los conceptos clave (los vas a ver en el código):

| Concepto | Qué es | Analogía |
|---|---|---|
| **Estado (State)** | Un `TypedDict` con toda la información del turno | La "memoria de trabajo" |
| **Nodo** | Una función `(estado) -> cambios al estado` | Un paso del flowchart |
| **Arista** | Conexión fija: "después de A, siempre B" | La flecha del flowchart |
| **Arista condicional** | Una función que decide el siguiente nodo | Un "if" en el flowchart |
| **Reducer** | Cómo se *fusionan* los cambios de un key del estado | Reglas de merge |
| **Checkpointer** | Persiste el estado en una base (por `thread_id`) | El "guardar partida" |
| **Subgraph** | Un grafo dentro de otro grafo | Una subrutina |
| **Streaming** | Emitir resultados mientras se ejecuta | Ver la película en vivo |

### ¿Por qué un grafo y no un `while`?

- El `while` del curso guarda el historial **en memoria de un objeto**: si el
  servidor se reinicia, adiós conversación.
- LangGraph **persiste el estado en cada paso** (checkpoint). Puedes:
  - Continuar una conversación entre requests (multi-turno).
  - Recuperarte de un crash.
  - Volver atrás en el tiempo (time-travel).
  - Escalar horizontalmente: cualquier instancia del servidor puede retomar
    el estado de un thread desde la base de datos.
- Y puedes **inspeccionar, auditar y controlar** cada paso (streaming, human-in-the-loop).

## ¿Qué es LangChain?

LangChain es un **SDK de integración**: una capa uniforme para hablar con
cualquier LLM (Anthropic, OpenAI, etc.). LangGraph se construye *sobre*
LangChain. Por eso verás:

- `BaseChatModel`: la interfaz común a todos los modelos. El resto de tu
  código nunca importa "ChatAnthropic" directo (excepto en la fábrica).
- `HumanMessage`, `AIMessage`, `SystemMessage`, `ToolMessage`: los tipos de
  mensaje estándar.
- `@tool`: decorador que convierte una función Python en una herramienta con
  esquema JSON automático (a partir de su firma y docstring).
- `usage_metadata`: los tokens usados, que LangChain adjunta a cada respuesta.

## ¿Y FastAPI?

FastAPI es el framework web: expone el grafo como endpoints HTTP. Su
superpoder para agentes: **streaming nativo** (Server-Sent Events) para mandar
los tokens al frontend en tiempo real. También da Swagger gratis en `/docs`.

---

# Parte 2 — La arquitectura de un vistazo

## El flujo de una petición, paso a paso

Imagina que el frontend hace:

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "¿Cuál es el horario de atención?"}'
```

Esto es lo que pasa **por dentro**:

```
1. FastAPI recibe el POST en app/api/routes/chat.py
   ├─ Rate limit por IP (¿este cliente no está abusando?)
   ├─ ¿viene thread_id? No → se genera uno con uuid4
   └─ Pide el grafo compilado a app/api/deps.py (cacheado)

2. graph.invoke({"input": "...", "agent": "base"}, config={"configurable": {"thread_id": "..."}})
   │
   ▼
3. Nodo "guardrails"  (app/graph/nodes.py)
   ├─ sanitiza el texto (control chars, longitud)
   ├─ detecta prompt injection (EN/ES)
   └─ ¿inseguro? → responde rechazo y TERMINA. ¿Seguro? → sigue
   │
   ▼
4. Nodo "agent"  (el LLM con las tools enlazadas)
   ├─ arma [System, historial..., Human(nuevo mensaje)]
   ├─ el LLM responde... ¿pidió herramientas? Sí → siguiente nodo "tools"
   │
   ▼
5. Nodo "tools"  (ToolNode)
   ├─ ejecuta la(s) tool(s) pedidas (calculate, get_current_time...)
   └─ devuelve el resultado como ToolMessage → vuelve al nodo "agent"
   │
   ▼
6. El LLM ya tiene el resultado → responde texto final → arista → END

7. FastAPI toma state["messages"][-1] como "answer" y responde JSON.
   El checkpointer ya guardó TODO el estado bajo ese thread_id.
```

En **streaming** (`/chat/stream`) el paso 4-6 se emite en vivo: cada nodo
(`event: node`) y cada fragmento de token (`event: message`).

## Mapa general de archivos

```
ai-service/
├── main.py                  # arranca uvicorn
├── pyproject.toml           # definición del proyecto + deps (uv)
├── requirements*.txt        # respaldo pip / Docker
├── .env.example             # plantilla de variables de entorno
├── langgraph.json           # config de despliegue LangGraph Platform
├── Dockerfile               # imagen del servicio
├── docker-compose.yml       # Postgres local (para el checkpointer)
├── README.md                # vista rápida
├── docs/
│   └── GUIA-APRENDIZAJE.md  # ← este documento
└── app/
    ├── core/                # config, logging, errores
    ├── llm/                 # fábrica de modelos + costos
    ├── tools/               # registro de herramientas
    ├── safety/              # guardrails
    ├── rag/                 # pipeline de recuperación
    ├── memory/              # checkpointer + memoria larga
    ├── graph/               # ★ estado, nodos y ensamblado LangGraph
    ├── agents/              # prompts y perfiles
    ├── orchestration/       # router de intenciones
    └── api/                 # FastAPI (rutas, schemas, deps)
└── tests/                   # pytest (sin API keys)
```

Regla de oro de esta arquitectura (Clean Architecture aplicada a IA):
**el grafo no sabe nada de HTTP, y las rutas no saben nada del LLM.**
Cada capa solo conoce a sus vecinas inmediatas.

---

# Parte 3 — Explicación archivo por archivo

## Raíz del proyecto

### `pyproject.toml`
Es el "package.json" de Python moderno (formato estándar de uv). Declara el
proyecto, la versión mínima de Python y las dependencias. `uv sync` lee este
archivo, resuelve versiones y crea `.venv/` con un **lock** (`uv.lock`) para
reproducibilidad. Los `requirements.txt` existen como respaldo para `pip` y
Docker.

### `.env.example`
Todas las variables que el servicio lee (ver `app/core/config.py`). Nunca
subas tu `.env` real (está en `.gitignore`).

### `langgraph.json`
Solo lo necesita **LangGraph Platform** (el servicio en la nube de LangChain).
Declara qué grafo exponer. Localmente no se usa; tu API FastAPI es la dueña.

### `docker-compose.yml`
Levanta un Postgres local (`docker compose up -d postgres`). Sirve para el
**checkpointer** cuando configures `DATABASE_URL` (ver `app/memory/checkpointer.py`).

### `Dockerfile`
Imagen del servicio. Instala `requirements-prod.txt` (solo dependencias de
producción, sin pytest/httpx) y corre `uvicorn`.

### `main.py`
Punto de entrada: crea la app FastAPI (`create_app()`) y permite
`python main.py` o `uvicorn main:app`. El `reload=True` solo en desarrollo.

---

## `app/core` — config, logging, errores

### `config.py` — la fuente única de verdad
```python
class Settings(BaseSettings):
    llm_provider: str = "anthropic"      # "anthropic" | "openai"
    anthropic_api_key: str | None = None
    database_url: str | None = None      # None → MemorySaver
    ...
```
- `pydantic-settings` lee las variables de entorno **y valida los tipos** al
  cargar. `LLM_PROVIDER` en `.env` → `settings.llm_provider`.
- `get_settings()` con `@lru_cache` → el objeto se lee **una sola vez** por
  proceso (no relee `.env` en cada request).
- **Convención**: nunca pongas `os.getenv()` en el código; siempre
  `from app.core.config import get_settings`.

### `logging.py`
`setup_logging()` configura el logger raíz una sola vez (formato legible,
silencia librerías ruidosas como httpx/openai). `get_logger(__name__)` te da
un logger con el nombre del módulo (enorme para depurar).

### `errors.py`
Jerarquía de excepciones del dominio: `CoppAiError` (base) y sus hijas
(`ConfigError`, `GuardrailError`, `ToolExecutionError`...). ¿Por qué? Para que
la API pueda capturar `CoppAiError` y responder un error **conocido** (500 con
mensaje claro) en vez de un traceback feo.

---

## `app/llm` — la fábrica de modelos

### `factory.py` — el "interruptor" multi-proveedor
```python
def get_chat_model(provider=None, *, model=None, ...) -> BaseChatModel:
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=..., api_key=..., temperature=...)
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=..., api_key=...)
```
- Todo el resto del código usa `BaseChatModel`. Si mañana cambias de
  proveedor, solo tocas este archivo.
- Los `import` de langchain-anthropic/openai están **dentro** de la función
  (lazy) para no pagar el costo si no se usan.
- Sin API key configurada → `ConfigError` con mensaje claro.

### `cost.py` — ¿cuánto cuesta cada sesión?
`SessionStats` acumula `input_tokens`/`output_tokens` y estima USD con una
tabla de precios aproximada. `add_message()` lee el `usage_metadata` que
LangChain adjunta a cada `AIMessage`. Es el mismo patrón de costos del curso,
pero alimentado automáticamente. Aún no está cableado al grafo (TODO del
roadmap) — está listo para usarse.

---

## `app/tools` — el registro de herramientas

Este es el **patrón del curso** que te dije que conserváramos, adaptado a
LangChain.

### `builtin.py` — las tools reales
```python
@tool
def get_current_time(timezone_name: str = "UTC") -> str:
    """Obtiene la fecha y hora actual en una zona horaria IANA.

    Args:
        timezone_name: zona horaria IANA, ej: "America/Mexico_City".
    """
    ...
```
- `@tool` genera automáticamente el **esquema JSON** (nombre, descripción,
  parámetros) desde la firma y el docstring. Ese esquema es lo que el LLM ve
  para decidir si llama la tool.
- `calculate` es una calculadora **sandboxeada**: parsea con `ast` (árbol de
  sintaxis), solo permite operaciones aritméticas y números, y limita
  exponentes/magnitudes para evitar abusos tipo `9**9**9`.
- Las tools de **negocio** (consultar pacientes, crear citas, CRM...) se
  agregan aquí o en nuevos módulos cuando exista la API .NET.

### `registry.py` — la colección central
```python
def _collect_tools():
    for name in dir(builtin):
        obj = getattr(builtin, name)
        if isinstance(obj, BaseTool):
            tools.append(obj)

ALL_TOOLS: list[BaseTool] = _collect_tools()
```
- Escanea `builtin.py` y junta todo lo decorado con `@tool`. **Añadir una tool
  = escribir una función**: el registro la encuentra sola.
- El LLM se enlaza con `model.bind_tools(ALL_TOOLS)` y `ToolNode(ALL_TOOLS)`
  las ejecuta. Defines una vez, usas en dos lugares.

---

## `app/safety` — los guardrails

### `guardrails.py` — portado directo del curso
| Función | Qué hace |
|---|---|
| `sanitize_input()` | Quita null bytes y chars de control **pero conserva `\n` y `\t`**, colapsa 3+ saltos de línea, corta inputs gigantes |
| `detect_prompt_injection()` | Regex EN + ES: "ignora instrucciones", "you are now", "ahora eres"... |
| `check_input_guardrails()` | Junta todo: devuelve `GuardrailResult(safe, sanitized, reason, pattern)` |
| `RateLimiter` | Ventana deslizante de peticiones (`check()`, `remaining`) |
| `check_output_guardrails()` | Gancho para validar la salida antes de enviarla al cliente |

Los patrones de inyección vienen del curso (probados en inglés y español).
`check_input_guardrails` es el **nodo de entrada** del grafo.

---

## `app/rag` — el pipeline RAG

RAG = **R**etrieval **A**ugmented **G**eneration: antes de responder, busca
documentos relevantes y se los da al LLM como contexto (para que no invente).

El pipeline (igual que el del curso, pero con interfaces limpias):

```
docs .md → chunker (por encabezados) → embeddings → vector store → retriever → contexto
```

### `chunker.py`
Divide el markdown en trozos (chunks) **agrupados por encabezado** (`#`, `##`),
guardando metadata `{source, heading, position}`. ¿Por qué por encabezados?
Porque un chunk con su encabezado conserva contexto ("Instalación → ejecuta
esto") y es mucho mejor que cortar por N caracteres ciegos.

### `embeddings.py`
- `EmbeddingsProvider`: interfaz (contrato) que cualquier proveedor debe
  cumplir (`embed_documents`, `embed_query`, `dimensions`).
- `OpenAIEmbeddingsProvider`: producción (text-embedding-3-small).
- `HashEmbeddingsProvider`: **dev sin API key**. Convierte tokens en un vector
  determinista (hashing). No sirve para producción, pero permite probar todo
  el pipeline offline.
- `get_embeddings("auto")`: si hay key de OpenAI usa la real, si no cae al hash.

### `vector_store.py`
- Interfaz `VectorStore`: `add`, `search`, `clear`, `count`.
- `InMemoryVectorStore`: numpy + similitud coseno. Suficiente para desarrollo.
- **Para producción**: implementar la misma interfaz con pgvector, Chroma o
  Qdrant. El resto del código no cambia (esa es la gracia de la interfaz).

### `retriever.py`
`Retriever.retrieve(query)` → embebe la pregunta → busca los top-k chunks →
`format_context()` los convierte en un bloque citable `[Fuente: X — Sección: Y]`.

### `ingest.py`
Recorre un directorio, lee `.md/.txt`, chunkea, embebe por lotes y guarda.
Devuelve `IngestStats` (cuántos archivos/chunks, errores tolerados).

### `service.py`
Singleton del pipeline para la API (un solo `Retriever` compartido entre
requests, protegido con un lock). **Ojo**: es en memoria — en producción debe
apuntar a un store compartido.

---

## `app/memory` — checkpointer y memoria

### `checkpointer.py` — la mejora #1 sobre el curso
```python
def get_checkpointer():
    if settings.database_url:
        from langgraph.checkpoint.postgres import PostgresSaver
        checkpointer = PostgresSaver.from_conn_string(settings.database_url)
        checkpointer.setup()   # crea las tablas
        return checkpointer
    from langgraph.checkpoint.memory import MemorySaver
    return MemorySaver()
```
- Sin `DATABASE_URL` → `MemorySaver`: guarda el estado en RAM (perfecto para
  desarrollo, se pierde al reiniciar).
- Con `DATABASE_URL=postgresql://...` → `PostgresSaver`: persistencia real.
- **Por qué importa**: el curso guardaba `this.messages` en el objeto — al
  reiniciar el proceso, muerto. Con Postgres, cualquier worker retoma el hilo.

### `store.py`
Memoria de **largo plazo** (entre threads/usuarios): preferencias, datos del
cliente, etc. Interfaz + implementación JSON (dev). En producción se migra al
`Store` nativo de LangGraph (postgres store).

---

## `app/graph` — el corazón LangGraph

### `state.py` — el esquema del Estado
```python
class AgentState(TypedDict, total=False):
    input: str
    messages: Annotated[list[AnyMessage], add_messages]   # ← reducer
    guardrail: dict[str, Any]
    tools_used: Annotated[list[str], operator.add]         # ← reducer
    rag_sources: list[str]
    provider: str
    agent: str
```
- Es un `TypedDict` (solo documenta la forma, no restringe en runtime).
- Los keys **sin** `Annotated` se comportan como "sobrescribir": si un nodo
  devuelve `{"input": x}`, reemplaza.
- Los keys **con reducer** definen cómo *fusionar*. `add_messages` es el más
  importante: append al historial + dedupe por id (ver Parte 4).

### `nodes.py` — las funciones de los nodos
```python
def guardrails_node(state):
    result = check_input_guardrails(state.get("input", ""))
    if not result.safe:
        return {"guardrail": {"safe": False, ...},
                "messages": [AIMessage(content=result.reason)]}
    return {"input": result.sanitized, "guardrail": {"safe": True, ...}}

def make_agent_node(model, system_prompt):
    bound_model = model.bind_tools(ALL_TOOLS)   # el LLM "aprende" las tools
    def agent_node(state):
        response = bound_model.invoke([
            SystemMessage(content=system_prompt),
            *state.get("messages", []),         # historial del checkpointer
            HumanMessage(content=state["input"]),
        ])
        return {"messages": [HumanMessage(content=state["input"]), response],
                "provider": model.__class__.__name__}
    return agent_node

def tools_node(state):
    # ejecuta las tool_calls del último AIMessage y registra cuáles se usaron
    result = ToolNode(ALL_TOOLS).invoke(state)
    return {**result, "tools_used": tool_names}
```
- Los nodos reciben el **estado completo** y devuelven **solo los cambios**
  (actualización parcial). LangGraph aplica los reducers.
- `make_agent_node` es un *factory*: devuelve el nodo con el modelo ya
  inyectado (para que los tests puedan pasar un modelo falso).
- Persistimos el `HumanMessage` + la respuesta para que el checkpointer guarde
  el historial completo (usuario y asistente).

### `graph.py` — el ensamblado
```python
workflow = StateGraph(AgentState)
workflow.add_node("guardrails", guardrails_node)
workflow.add_node("agent", make_agent_node(model or get_chat_model(), system_prompt))
workflow.add_node("tools", tools_node)

workflow.add_edge(START, "guardrails")
workflow.add_conditional_edges("guardrails", _route_after_guardrails, {"agent": "agent", END: END})
workflow.add_conditional_edges("agent", _route_after_agent, {"tools": "tools", END: END})
workflow.add_edge("tools", "agent")

return workflow.compile(checkpointer=checkpointer or get_checkpointer())
```
- `StateGraph(AgentState)` crea el grafo con ese esquema de estado.
- `add_node(name, fn)` registra los nodos.
- `add_edge(A, B)`: flecha fija.
- `add_conditional_edges(A, fn, mapping)`: `fn(estado)` devuelve el nombre del
  siguiente nodo; `mapping` traduce ese nombre a nodos (aquí `END`).
- `compile(checkpointer=...)`: lo convierte en un objeto invocable
  (`graph.invoke`, `graph.ainvoke`, `graph.astream`). Sin `compile`, no corre.
- `build_graph(model=None, checkpointer=None)` acepta inyecciones para tests.

### `subgraphs/rag_agent.py` — el sub-agente de ejemplo
Un grafo completo con **estado aislado** (`RagState` no comparte `messages`
con el supervisor): `retrieve → answer`. `make_rag_agent_node()` lo envuelve
para usarlo *dentro* del supervisor: recibe `state["input"]`, invoca el
subgrafo y publica solo la respuesta + fuentes. Ver Parte 4.

---

## `app/agents` — prompts y perfiles

### `prompts.py`
`BASE_SYSTEM_PROMPT`: el "sistema operativo" del agente — su personalidad,
reglas (no inventar datos, citar fuentes, prudencia en temas de salud,
rechazar manipulación...). Cada agente especializado tendrá el suyo.

### `registry.py`
```python
@dataclass(frozen=True)
class AgentProfile:
    key: str; name: str; description: str
    system_prompt: str
    provider: str | None = None
    model: str | None = None
    tools: tuple[str, ...] = ()

AGENTS = {"base": AgentProfile(...)}   # + TODOs: doctor, psychologist, crm...
```
Catálogo de agentes. Cuando exista lógica de negocio, agregas perfiles aquí y
sus subgrafos en `graph/`.

---

## `app/orchestration` — el router

`router.py` → `IntentRouter.route(state)`. Hoy devuelve siempre `"base"`.
Es el lugar donde mañana vivirán las reglas "si pregunta de documentación →
subgrafo RAG; si pide datos del paciente → agente doctor...". El esqueleto
está, la lógica la pondrás tú cuando exista el dominio.

---

## `app/api` — FastAPI

### `main.py`
`create_app()` fabrica la app: CORS abierto (dev), monta las rutas bajo
`/api/v1` y arranca logging con el `lifespan`. `app = create_app()` al final
es lo que uvicorn importa.

### `schemas.py`
Pydantic request/response: `ChatRequest` (message, thread_id, agent),
`ChatResponse`, `HealthResponse`, `IngestRequest/Response`, `ThreadStateResponse`.
Pydantic valida la entrada (ej: `message` min_length=1) y documenta en Swagger.

### `deps.py`
```python
@lru_cache(maxsize=1)
def get_graph():
    try:
        return build_graph()
    except ConfigError as exc:
        raise HTTPException(503, f"Servicio no configurado: {exc}")
```
- Dependencia FastAPI que da el grafo compilado **una sola vez** (cacheado).
- Los tests la sobreescriben con `app.dependency_overrides[get_graph] = ...`
  (inyección de dependencias — el truco que permite testear sin API keys).
- Sin key configurada → 503 claro en vez de 500 críptico.

### `routes/chat.py` — el corazón HTTP
```python
@router.post("", response_model=ChatResponse)
async def chat(request, http_request, graph=Depends(get_graph)):
    _check_rate_limit(http_request)          # 429 si abusa
    thread_id = request.thread_id or str(uuid.uuid4())
    result = await graph.ainvoke(
        {"input": request.message, "agent": request.agent},
        config={"configurable": {"thread_id": thread_id}, "recursion_limit": ...},
    )
    return ChatResponse(thread_id=..., answer=_extract_answer(result), ...)
```
- `thread_id` es la clave de persistencia (ver Parte 4).
- `recursion_limit` = tope de pasos del grafo (el "MAX_TOOL_CALLS" del curso,
  pero a nivel de grafo — evita loops infinitos).
- `/stream` usa `graph.astream(stream_mode=["updates","messages"])` y
  transforma los eventos a formato SSE (ver Parte 4).
- `_rate_limiters`: dict IP → `RateLimiter` con poda automática (no crece
  infinitamente).

### `routes/threads.py`
`GET /threads/{id}/state` → `graph.aget_state(config)` lee el historial
**ya persistido** por el checkpointer. Ejemplo de lo que el curso no podía
hacer: "dame la conversación completa de este thread aunque el proceso se haya
reiniciado".

### `routes/ingest.py`
`POST /ingest {path}` → `ingest_directory()` para indexar documentación en el
RAG. `routes/health.py` → healthcheck simple.

---

## `tests/` — cómo se prueba sin API keys

| Archivo | Qué valida |
|---|---|
| `conftest.py` | Fixture `simple_graph`: grafo con LLM **falso** |
| `fakes.py` | `FakeToolAwareModel`: fake de langchain que tolera `bind_tools` |
| `test_graph.py` | Flujo del grafo: respuesta, bloqueo por inyección, loop de tools, **historial multi-turno** |
| `test_guardrails.py` | Sanitización, inyección EN/ES, rate limiter |
| `test_rag.py` | Chunker por encabezados, ingest + retrieve, determinismo de embeddings |
| `test_api.py` | Endpoints con `dependency_overrides` (LLM falso) |

El truco: `FakeMessagesListChatModel` de langchain-core devuelve mensajes
predefinidos sin llamar a ninguna API. Así tests de grafo y API corren
**offline y en milisegundos**. Los tests son también tu mejor ejemplo de uso
del grafo.

---

# Parte 4 — Los conceptos de LangGraph con código real

## El Estado y los reducers

**Problema**: un nodo devuelve `{"messages": [AIMessage(...)]}`. ¿Qué pasa si
el estado ya tenía mensajes? ¿Sobrescribimos (perdemos el historial) o
agregamos?

**Solución**: los reducers. Un reducer es una función que dice cómo fusionar
lo nuevo con lo viejo:

```python
messages: Annotated[list[AnyMessage], add_messages]
```

`add_messages` hace dos cosas:
1. **Append**: agrega los mensajes nuevos al final del historial.
2. **Dedupe por `id`**: si un mensaje trae un `id` que ya existe, lo
   *reemplaza* (LangChain asigna ids a los mensajes automáticamente).

Esto resuelve el bug clásico: en una conversación con herramientas, el
`AIMessage` con `tool_calls` y su resultado `ToolMessage` deben quedar
**adyacentes**; si el modelo repite una llamada, no duplicamos.

`tools_used` usa `operator.add` como reducer: acumula strings. Fíjate que
elegir el reducer correcto **es diseño de estado** — es la parte que más
diferencia a LangGraph de un `while` a mano.

## El checkpointer y el `thread_id`

El checkpointer guarda **una copia del estado después de cada paso**, claveada
por `thread_id`. Por eso:

```python
config = {"configurable": {"thread_id": "abc-123"}}
```

- **Turno 1**: `invoke(..., config)` → guarda estado con 2 mensajes.
- **Turno 2**: mismo `thread_id` → LangGraph *carga* el estado guardado y el
  nodo `agent` arma el prompt con el historial completo.
- Threads distintos → estados distintos (conversaciones paralelas aisladas).

La API hace esto gratis: si el cliente manda `thread_id` lo usa; si no,
genera uno nuevo con `uuid4` y se lo devuelve en la respuesta (el frontend lo
guarda para seguir la conversación).

## El loop de herramientas: `bind_tools` + `ToolNode`

1. `model.bind_tools(ALL_TOOLS)`: le pasa al LLM los **esquemas JSON** de las
   tools (los genera `@tool`). El LLM no "ejecuta" nada: solo *pide*.
2. La respuesta del LLM puede traer `tool_calls` (nombre + argumentos).
3. `_route_after_agent` detecta `last.tool_calls` → va al nodo `tools`.
4. `ToolNode(ALL_TOOLS)` ejecuta las tools y devuelve `ToolMessage`s.
5. Vuelve al nodo `agent` con los resultados; el LLM ya puede responder con
   base real.
6. Cuando el LLM responde *sin* `tool_calls` → `END`.

El `recursion_limit` corta el ciclo si el LLM se vuelve loco pidiendo tools
(el límite del curso, pero manejado por el framework).

## Streaming: cómo llegan los tokens al frontend

`graph.astream(..., stream_mode=["updates", "messages"])` emite pares
`(modo, dato)`:

| modo | dato | Para qué |
|---|---|---|
| `"updates"` | `{nombre_nodo: cambios}` | mostrar "Buscando...", "Ejecutando tool..." |
| `"messages"` | `(fragmento_de_mensaje, metadata)` | los tokens en vivo |

El endpoint `/chat/stream` los convierte a **SSE** (Server-Sent Events):

```
event: node
data: {"type":"node","node":"agent"}

event: message
data: {"type":"token","content":"El "}

event: message
data: {"type":"token","content":"horario "}
...
event: done
data: {"thread_id":"..."}
```

El frontend hace un `fetch` normal y lee el body como un stream de líneas
(`ReadableStream` o `EventSource`). Es el patrón estándar de chat moderno.

## Subgraphs: agentes con estado aislado

¿Por qué no un solo agente con 50 tools? Porque el contexto se infla y el LLM
se confunde eligiendo tools (el "super-agente" anti-patrón). La solución es
**orquestador + especialistas**:

```
Supervisor (estado compartido: messages, thread)
   │ enruta según intención
   ├─ subgrafo RAG      (estado propio: question, context, answer)
   ├─ subgrafo datos    (estado propio: query, results)
   └─ subgrafo doctor   (estado propio: ...)
```

Cada subgrafo filtra el ruido (lee 50 archivos) y devuelve **solo el
resumen** al supervisor. `subgraphs/rag_agent.py` es ese patrón ya construido:
`make_rag_agent_node()` recibe `state["input"]`, invoca el subgrafo con su
propio estado y publica la respuesta en el padre.

---

# Parte 5 — Recetas para extenderlo

### ➕ Agregar una tool nueva
1. En `app/tools/builtin.py`, escribe una función con `@tool`, docstring claro
   y tipos en los parámetros (de ahí sale el esquema).
2. Listo: `registry.py` la detecta sola y el agente la "aprende" al compilar.
3. Test: llama `tools_node` con un AIMessage que la pida, o úsala en un
   `test_graph` con el fake model.

### ➕ Agregar un agente especializado (doctor, psicólogo, CRM)
1. Crea `app/graph/subgraphs/doctor_agent.py` copiando el patrón de
   `rag_agent.py` (estado propio + subgrafo + node wrapper).
2. Agrega su `AgentProfile` en `app/agents/registry.py` (prompt + tools).
3. En `app/orchestration/router.py`, escribe las reglas de enrutamiento.
4. En `app/graph/graph.py`, añade el nodo y la arista condicional del router.

### ➕ Conectar el RAG al supervisor
1. Indexa documentación: `POST /api/v1/ingest {"path": "./docs"}`.
2. En `graph.py`, agrega `rag_agent` como nodo (usando
   `make_rag_agent_node()` de `subgraphs/rag_agent.py`).
3. En `router.py`, detecta intención de "pregunta de documentación" →
   devuelve el nodo rag. El supervisor ya sabe citar fuentes.

### ➕ Persistencia real con Postgres
```bash
docker compose up -d postgres
# en .env:
DATABASE_URL=postgresql://coppai:coppai@localhost:5432/coppai
```
`get_checkpointer()` detecta la URL y usa `PostgresSaver` (crea las tablas
solas con `setup()`). Los tests siguen pasando porque inyectan `MemorySaver`.

### ➕ Human-in-the-loop (aprobaciones)
Dentro de un nodo: `interrupt("¿Apruebas esta acción?")` pausa el grafo y
persiste. Para reanudar: `graph.invoke(Command(resume="aprobado"), config=...)`.
Útil para acciones sensibles (enviar correos, guardar datos de pacientes).

### ➕ Llamar al backend .NET desde una tool
Crea `app/tools/backend.py` con tools que hagan `httpx.get/post` a tu
`CoppAddresd.Api` (con auth). Como son tools normales, entran al registry y el
agente las usa. Cuando la API .NET tenga endpoints reales, solo escribes estas
funciones.

---

# Parte 6 — Glosario rápido

| Término | Significado |
|---|---|
| **LLM** | Modelo de lenguaje: texto → texto |
| **Agente** | LLM + tools + bucle |
| **Agentic loop** | Ciclo "LLM pide tool → se ejecuta → se devuelve" |
| **Estado (State)** | El dict que viaja por el grafo |
| **Nodo** | Función `(estado) -> cambios` |
| **Arista** | Conexión fija entre nodos |
| **Arista condicional** | Decisión: función que elige el siguiente nodo |
| **Reducer** | Cómo fusionar cambios de un key (`add_messages`, `operator.add`) |
| **Checkpointer** | Persistencia del estado por `thread_id` |
| **`thread_id`** | Identificador de conversación (clave del estado guardado) |
| **Subgraph** | Grafo usado como nodo de otro grafo |
| **Tool** | Función con esquema JSON que el LLM puede pedir ejecutar |
| **`bind_tools`** | "Enseña" las tools al LLM |
| **`ToolNode`** | Ejecuta las tools pedidas |
| **RAG** | Buscar contexto (docs) antes de responder |
| **Chunk** | Trozo de documento indexado |
| **Embedding** | Vector numérico que representa un texto |
| **Guardrails** | Validaciones de seguridad de entrada/salida |
| **Streaming (SSE)** | Enviar resultados en vivo al cliente |
| **Recursion limit** | Tope de pasos del grafo (anti loop infinito) |
| **Interrupt / Command** | Pausar el grafo para aprobación humana y reanudarlo |
| **Dependency override** | Truco de FastAPI para inyectar falsos en tests |

---

# Parte 7 — Errores comunes y cómo depurar

| Síntoma | Causa probable | Solución |
|---|---|---|
| `ConfigError: ANTHROPIC_API_KEY no está configurada` | `.env` incompleto o no creado | `cp .env.example .env` y agrega tu key |
| API responde 503 "Servicio no configurado" | Falta la key del proveedor (`llm_provider`) | Revisa `.env` o cambia `LLM_PROVIDER` |
| El chat no recuerda el turno anterior | El frontend no reenvía `thread_id` | Usa el `thread_id` que devuelve `/chat` |
| El agente repite llamadas a tools | `recursion_limit` bajo o el LLM está confundido con tools | Sube `RECURSION_LIMIT` o reduce las tools expuestas |
| El historial se ve "duplicado" o "colapsado" | Problema de ids en `add_messages` (objetos reusados) | Crea mensajes nuevos por turno (ver `tests/conftest.py`) |
| `FakeMessagesListChatModel` lanza `NotImplementedError` | `bind_tools` no implementado en el fake | Usa `tests/fakes.py::FakeToolAwareModel` |
| El streaming no llega al frontend | Proxy intermedio buffereando | Headers `Cache-Control: no-cache`, `X-Accel-Buffering: no` |
| RAG devuelve basura | Store vacío o embeddings hash en producción | `POST /ingest` primero; usa embeddings reales en prod |

**Truco de depuración**: agrega un nodo temporal o usa `graph.astream(
stream_mode="updates")` para ver **qué nodo corre y qué devuelve** en cada
paso. Los tests de `test_graph.py` son el ejemplo más claro de cómo
inspeccionar el estado final.

---

> 💡 **Siguiente paso sugerido**: corre los tests (`uv run pytest`) y abre
> `test_graph.py::test_checkpointer_keeps_history` — es el test que demuestra
> la diferencia real con el proyecto del curso (persistencia multi-turno).
> Luego mira `subgraphs/rag_agent.py` y juega a conectar el RAG al supervisor.
