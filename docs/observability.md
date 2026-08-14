# Observabilidad — AI Service

Documentación de la fase 7 del plan de agentes: registro de ejecuciones en
`ai.agent_executions` + endpoints admin que responden las 12 preguntas del
prompt (sección 13).

## Las 12 preguntas del monitoreo

| # | Pregunta | Fuente |
|---|---|---|
| 1 | ¿Qué agente respondió? | `agent_type_id` |
| 2 | ¿Qué versión estaba activa? | `version_id` (del `CompiledAgent` del runtime) |
| 3 | ¿Qué modelo utilizó? | `model` (config del agente) o `provider` real (`output.provider`) |
| 4 | ¿Qué documentos recuperó? | `output.rag_sources` (parseado del bloque `[FUENTES: ...]` de la tool RAG) |
| 5 | ¿Qué memoria recuperó? | `input.memory_context` / `input.experience_context` |
| 6 | ¿Qué tools ejecutó? | `output.tools_used` |
| 7 | ¿Cuánto tardó? | `latency_ms` |
| 8 | ¿Cuántos tokens consumió? | `tokens_in` / `tokens_out` (`usage_metadata` de los AIMessages) |
| 9 | ¿Qué errores ocurrieron? | `status=error` + `error` |
| 10 | ¿Feedback del usuario? | `feedback_rating` / `feedback_comment` (join `agent_feedback` por thread) |
| 11 | ¿Evaluación programática? | `evaluations` / `best_evaluation` (join `agent_evaluations` por execution) |
| 12 | ¿Experiencias generadas? | `experiences` (del agente, join `agent_experiences`) |

## Componentes

| Pieza | Descripción |
|---|---|
| `app/observability/executions.py` | `ExecutionTracker` (start/complete/fail) + `extract_rag_sources` + `_message_text` (extrae texto de bloques de Claude). Best-effort: si la persistencia falla, el chat sigue. |
| `app/api/routes/admin.py` | Endpoints admin de consulta (ver abajo). Sin auth propia: el backend .NET los expone tras `Agents.View`; en producción el servicio solo debe escuchar en red interna. |

## Flujo

```
POST /api/v1/chat (o /stream)
  │
  ├─ ExecutionTracker.start  → fila en `ai.agent_executions` (status=ejecutando)
  │                            con input: {message, memory_context, provider, model}
  ├─ graph.ainvoke/astream
  ├─ ExecutionTracker.complete → status=completado, output {answer, tools_used,
  │                            rag_sources}, tokens (usage_metadata), latency_ms
  └─ respuesta incluye `execution_id` (para vincular feedback)
```

## Endpoints

```
GET /api/v1/admin/executions
    ?agent_type_id=&user_id=&status=completado|error|ejecutando
    &from_date=&to_date=&limit=&offset=
    → {total, items: [ExecutionSummary]}   # por defecto últimos 7 días

GET /api/v1/admin/executions/{execution_id}
    → ExecutionDetail (input/output completos + feedback + evaluaciones + experiencias)
```

El feedback (`POST /api/v1/chat/feedback`) acepta ahora `execution_id` para
vincular el rating a la ejecución concreta.

## Cambios de infraestructura en esta fase

### Checkpointer async (fix importante)

El grafo se ejecuta con `ainvoke`/`astream`, y LangGraph llama a
`aget_tuple`/`aput`. El `PostgresSaver` **síncrono** no los implementa
(`NotImplementedError` en cada request). Se migró a:

- `AsyncPostgresSaver` + `AsyncConnectionPool` en `app/memory/checkpointer.py`.
- `get_checkpointer()` ahora es **async** y se resuelve desde los puntos
  async: `get_graph()` en `app/api/deps.py` (ahora async, cacheado en módulo)
  y `AgentRuntimeRegistry._compile` (vía `checkpointer_factory` inyectable —
  los tests pasan `MemorySaver`).
- `build_graph()` exige un checkpointer explícito (error claro si falta):
  nunca llamar `get_checkpointer()` síncrono (falla con "no running loop").

### Windows / event loop

En Windows, uvicorn 0.36+ fuerza `ProactorEventLoop` (incompatible con
psycopg async). Levantar SIEMPRE con:

```bash
uv run python run_dev.py   # usa loop="asyncio:SelectorEventLoop"
```

## Tests

```bash
uv run pytest tests/test_observability.py -v   # 9 tests, sin BD ni API keys
```

Cubren: parseo de fuentes RAG (incl. JSON inválido), start/complete/fail del
tracker (tokens, latencia, answer), content en bloques de Claude, robustez
best-effort.
