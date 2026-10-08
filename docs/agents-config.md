# Configuración de agentes — Arquitectura y observaciones

> Estado: **Opción A (perfiles configurables en BD)** — los prompts y la
> configuración de los agentes viven en `ai.agent_runtime_configs`, editables
> vía API. El routing de intenciones sigue usando **keywords** por ahora, con
> el clasificador por LLM (o híbrido) como TODO a futuro.

## Contexto

El chat multi-agente enruta cada mensaje a un perfil según la intención. El
perfil define: identidad, `system_prompt`, tools y (a futuro) KBs RAG.

Históricamente los perfiles vivían **hardcodeados en código**
(`app/agents/registry.py` + `app/agents/prompts.py`). Eso hacía imposible
configurar un agente desde un front sin tocar código y desplegar.

Esta decisión migra la **fuente de verdad** de los perfiles a la base de datos
(`ai.agent_runtime_configs`), manteniendo los keywords como mecanismo de
clasificación de intención.

## Modelo de decisión (dos ejes independientes)

Es crítico NO confundir dos decisiones de diseño distintas:

1. **Routing (¿qué agente atiende el mensaje?)**
   - `IntentRouter.route(state)` devuelve la **clave** del perfil
     (`nutrition | medical | psychology | base`).
   - Mecanismo actual: **keywords** (baratas, deterministas).
   - TODO futuro: clasificador por **LLM** o **híbrido** (keywords + LLM).
     Ver bloque comentado `_classify_with_llm` en `app/orchestration/router.py`.

2. **Resolución (¿qué prompt/tools usa esa clave?)**
   - `resolver.resolve_agent_profile(session, key)` devuelve el `AgentProfile`
     completo leyendo la **BD**.
   - Fuente de verdad: `ai.agent_runtime_configs[key].config`.
   - Fallback: si la clave no está en BD, se usa el perfil de `registry.py`.

## Flujo de resolución

```
mensaje → guardrails
        → IntentRouter.route(state)        → clave (keywords)
        → resolver.resolve_agent_profile() → lee BD (sesión corta)
              ├─ existe en BD → AgentProfile desde ai.agent_runtime_configs
              └─ no existe    → get_agent_profile(clave)  [fallback código]
        → make_agent_node usa prompt/tools del perfil
```

### Por qué sesión corta por turno (decisión de diseño)

El resolver abre una **conexión a BD por turno** (patrón que ya usa
`memory_load`). Esto significa que **editar el prompt de un agente en BD se
refleja en el próximo mensaje sin reiniciar ni recompilar el grafo**. El costo
es una consulta extra por turno — despreciable frente al LLM.

Alternativa descartada por ahora: cachear al compilar el grafo (requeriría
invalidar cache al cambiar el prompt, más fricción para probar desde un front).

## Modelo de datos

Tabla `ai.agent_runtime_configs` (ya existente, sin cambios de schema):

| Columna | Descripción |
|---|---|
| `agent_type_id` | Clave del agente (p.ej. `nutrition`) |
| `version_id` | Versión activa |
| `is_active` | Marca la versión vigente |
| `config` | JSON con la configuración ejecutable |

El JSON `config` se interpreta con `app/agents/runtime_config.py`
(`AgentRuntimeConfig`):

```json
{
  "system_prompt": "...",
  "prompt": "...",                    // opcional, se concatena tras system_prompt
  "provider": null,                   // null → proveedor por defecto
  "model": null,                      // null → modelo por defecto
  "temperature": null,
  "max_tokens": null,
  "tools": [],
  "retrieval_config": { "enabled": false, "knowledge_base_ids": [], "top_k": 5 },
  "memory_config": { "enabled": false, "categories": [] }
}
```

## Endpoints

### Consulta (configuración futura del front)

```
GET /api/v1/agents              # lista agentes activos (key, name, config)
GET /api/v1/agents/{key}        # detalle de un agente
```

Protegidos igual que el resto del API. Son de **lectura**; la escritura se
hará desde el backend (.NET) vía los endpoints internos (ver abajo).

### Internos (backend → AI Service, header `X-Internal-Key`)

```
POST   /internal/agents/sync-config      # upsert de la versión activa de un agente
POST   /internal/agents/ingest           # indexar documento → pgvector (RAG)
DELETE /internal/agents/ingest/{id}      # eliminar chunks de un documento
GET    /internal/agents/{id}/graph       # descriptor del grafo (playground ERP)
```

El backend (.NET) es la fuente de verdad del **catálogo**; el AI Service
cachea la config activa para compilar grafos sin round-trips.

### Descriptor del grafo (`GET /internal/agents/{id}/graph`)

`app/agents/graph_descriptor.py` traduce la config activa a nodos + aristas
que el playground del ERP dibuja. Cada nodo trae `meta` con su detalle de
configuración (para el drawer de nodo): `agent` expone
`provider/model/temperature/max_tokens/max_tool_calls/recursion_limit`,
`rag_enabled/knowledge_base_count/top_k` y `memory_enabled`; `tools` lista
`tools` + `max_tool_calls`; los nodos de memoria llevan `categories`. Sin
config sincronizada responde el grafo `base` (todas las tools, sin memoria).

## Poblar la BD (seed)

Al inicializar se registran los perfiles base en `ai.agent_runtime_configs`.
Se puede hacer:
- Llamando a `POST /internal/agents/sync-config` con cada perfil, o
- vía un script de seed que inserte las filas.

Los perfiles actuales: `base`, `nutrition`, `medical`, `psychology`.

## TODO / Mejoras futuras

- [ ] Migrar la clasificación de intención de keywords → **LLM** o **híbrido**
      (keywords + LLM) para robustez ante vocabulario variado.
- [ ] Endpoints de **escritura** públicos para que un front de administración
      cree/edite agentes directamente (hoy la escritura es interna).
- [ ] KBs RAG por agente (`retrieval_config.knowledge_base_ids`) para dar
      contexto documental a cada perfil.
- [ ] Versionado / historial de prompts por agente.

## Correcciones QA ERP/app — 2026-10-08

Para agentes ejecutados mediante `agent_type_id`, el runtime resuelve el alcance
RAG consultando el catálogo compartido `agents.knowledge_bases`. El backend .NET
sigue siendo dueño de esa tabla; el AI Service solo requiere lectura y no crea
migraciones fuera del esquema `ai`.

- Solo se incluyen bases `Activo`: globales y las propias del agente.
- Una selección vacía incorpora todas las propias y globales activas. Una
  selección explícita restringe las propias y conserva las globales activas.
- El vector store siempre recibe IDs concretos; sin bases permitidas, no se
  inyecta `retrieve_knowledge`. Nunca se consulta sin filtro.
- El alcance se verifica al obtener el agente, incluso con grafo cacheado; una
  activación, desactivación o eliminación de KB se refleja en el siguiente turno.
  El descriptor del playground usa la misma resolución.

Las calificaciones del monitoreo se asocian por `execution_id`; compartir
`thread_id` no transmite la calificación a otras respuestas. El feedback antiguo
sin ejecución no se asigna a una respuesta arbitraria.

El checkpoint conserva la conversación. Al entrar en cada turno se reinician
`suggestions`, `tools_used` y fuentes RAG; los tokens y fuentes de la ejecución
se calculan desde el límite del turno actual. Esto evita que una propuesta de
cita previa reaparezca al responder, por ejemplo, una operación matemática.

Validación automatizada: `uv run pytest`, sin API keys. Los fixtures establecen
la configuración de test antes de importar el motor y desactivan la migración
por defecto. Los tests de carga de laboratorios inyectan un modelo falso también
para archivos inválidos. No sustituyen una prueba con proveedores y datos reales.
