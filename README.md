# CoppAddresd AI Service

Agente de IA escalable para **CoppAddresd**, construido con **LangGraph** + **FastAPI**.
Servicio hermano de `coppAddresdBack` (.NET) — juntos formarán la plataforma
para clientes y profesionales (doctor, psicólogo, etc.) con documentación RAG,
conocimiento de la base de datos de la API y captura de datos hacia un futuro CRM.

> **Estado actual:** arquitectura inicial sólida + agente supervisor mínimo
> funcionando. La lógica de negocio se agrega conforme evoluciona el backend.

> 📚 **¿Estás aprendiendo?** Lee **[docs/GUIA-APRENDIZAJE.md](docs/GUIA-APRENDIZAJE.md)** —
> explicación completa de cada concepto y cada archivo, aparte de este README.

---

## 🧱 Stack

| Capa            | Tecnología                                   |
| --------------- | -------------------------------------------- |
| Grafo agente    | LangGraph (StateGraph, checkpointer, subgraphs) |
| API             | FastAPI + uvicorn (streaming SSE)            |
| LLM            | Multi-proveedor: Anthropic Claude / OpenAI   |
| Memoria        | Checkpointer LangGraph (MemorySaver dev → Postgres prod) |
| RAG             | Chunker propio + embeddings + vector store (pgvector en prod) |
| Seguridad      | Guardrails: sanitización, prompt injection (EN/ES), rate limit |
| Tooling        | uv, pytest, Docker                           |

---

## 🚀 Inicio rápido

```bash
cd ai-service
cp .env.example .env        # agrega tus API keys
uv sync                    # instala dependencias (crea .venv)
uv run uvicorn main:app --reload
```

Sin `uv`: `python -m venv .venv && pip install -r requirements.txt`

Abre **http://localhost:8000/docs** (Swagger) o prueba:

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "¿qué puedes hacer?"}'
```

### Tests

```bash
uv run pytest
```

Los tests corren **sin API keys**: usan un LLM falso (`FakeMessagesListChatModel`)
y embeddings hash deterministas para validar el flujo completo.

---

## 🏗️ Estructura

```
ai-service/
├── app/
│   ├── api/               # FastAPI: /chat, /chat/stream, /threads, /ingest, /health
│   ├── graph/             # ★ Núcleo LangGraph
│   │   ├── state.py       #    Estado global con reducers (add_messages)
│   │   ├── nodes.py       #    guardrails → agent → tools
│   │   ├── graph.py       #    Ensamblado del grafo supervisor
│   │   └── subgraphs/     #    Sub-agentes con estado aislado (rag_agent de ejemplo)
│   ├── agents/            # Prompts + registro de perfiles (base, y lugar para doctor/crm...)
│   ├── orchestration/     # Router de intenciones del supervisor (por crecer)
│   ├── tools/             # Registry: schema + executor (patrón del curso)
│   ├── memory/            # Checkpointer (MemorySaver / PostgresSaver) + store
│   ├── rag/               # chunker, embeddings, vector store, retriever, ingest
│   ├── llm/               # Factory multi-proveedor (Anthropic/OpenAI) + costos
│   ├── safety/            # Guardrails (patrón portado del curso node-dev-assistant)
│   └── core/              # config (pydantic-settings), logging, errors
├── tests/                 # pytest: graph, guardrails, rag, api
├── main.py
├── pyproject.toml         # uv (fuente de verdad) + requirements.txt (pip/Docker)
├── langgraph.json         # Despliegue en LangGraph Platform
└── .env.example
```

## 🔀 Arquitectura del agente

```
Cliente (frontend/UI)
      │  POST /chat o /chat/stream (SSE)
      ▼
┌─────────────────────────────────────────────┐
│               API FastAPI                    │
│  rate limiting por IP → thread_id → invoke   │
└─────────────────────────────────────────────┘
      ▼
┌─────────────────────────────────────────────┐
│        GRADO SUPERVISOR (LangGraph)          │
│                                             │
│  START → guardrails → agent ⇄ tools → END   │
│         │            (bind_tools + ToolNode) │
│         └─ inseguro → END (rechazo)          │
│                                             │
│  ┌─────────┐   ┌─────────┐   ┌───────────┐  │
│  │ subgraph│   │ subgraph│   │  subgraph │  │  ← especializados por crecer:
│  │  rag    │   │  datos  │   │  doctor…  │  │     RAG, API .NET, salud, CRM
│  └─────────┘   └─────────┘   └───────────┘  │
│                                             │
│  Checkpointer (Postgres en prod)            │
│  → multi-turno, crash recovery, time-travel │
└─────────────────────────────────────────────┘
```

### Fundamentos heredados del curso `node-dev-assistant`

- **Tool Registry**: definiciones separadas de la ejecución → `tools/registry.py` + `tools/builtin.py`.
- **Guardrails**: sanitización, prompt injection (EN/ES), rate limit → `safety/guardrails.py`.
- **RAG por encabezados**: `rag/chunker.py` → embeddings → vector store → retriever.
- **Límite de tool calls**: `recursion_limit` en la config del grafo.
- **Costos por sesión**: `llm/cost.py` (SessionStats + estimación USD).

### Lo que LangGraph añade (y el curso no tenía)

- **Checkpointing real**: el historial sobrevive entre requests y reinicios
  (con Postgres: escala horizontal + crash recovery).
- **Subgraphs con estado aislado**: cada sub-agente filtra ruido y devuelve
  solo resúmenes al supervisor (evita el "super-agente" con contexto inflado).
- **Streaming granular**: tokens, nodos y estado en vivo vía SSE.
- **Human-in-the-loop** (`interrupt` / `Command`) cuando se necesiten aprobaciones.

---

## 🔌 Endpoints

| Método | Ruta                       | Descripción                                  |
| ------ | -------------------------- | -------------------------------------------- |
| POST   | `/api/v1/chat`             | Respuesta completa del agente (incluye `execution_id`) |
| POST   | `/api/v1/chat/stream`      | Streaming SSE (tokens + nodos en vivo)       |
| POST   | `/api/v1/chat/feedback`    | Feedback 1-5 → experiencia aprendida (adaptive memory) |
| GET    | `/api/v1/threads/{id}/state` | Historial persistido de una conversación   |
| POST   | `/api/v1/ingest`           | Indexa docs `.md/.txt` para el RAG           |
| GET    | `/api/v1/admin/executions` | Lista ejecuciones (filtros + paginación)     |
| GET    | `/api/v1/admin/executions/{id}` | Detalle completo (12 preguntas del monitoreo) |
| GET    | `/api/v1/health`           | Healthcheck                                  |

Endpoints internos (backend → AI Service, `X-Internal-Key`): `POST /internal/agents/sync-config`, `POST /internal/agents/ingest`, `DELETE /internal/agents/ingest/{id}`.

## 🗺️ Roadmap

1. ✅ **RAG conectado**: ingest (md/txt/PDF) + pgvector + retriever aislado por KB.
2. ✅ **Memoria usuario**: `agent_memories` con aislamiento estricto + resumen rodante.
3. ✅ **Postgres compartido**: el AI Service usa el mismo PostgreSQL del backend (imagen `pgvector/pgvector:pg18` en el compose raíz del workspace, puerto 5432, db `coppaddresd`) con `DATABASE_URL` en `.env` para persistencia real.
4. ✅ **Adaptive memory**: feedback → experiencias del agente + evaluación heurística.
5. ✅ **Observabilidad**: `agent_executions` (tokens/latencia/fuentes/tools) + endpoints admin.
6. ⏳ **Frontend admin**: módulo de agentes + playground.
7. ⏳ **Hardening**: rate limit por usuario, redacción de PII, auditoría.
