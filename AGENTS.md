# AI Service — Agent Guide

Python 3.11+, LangGraph, FastAPI. Agente de IA escalable para CoppAddresd.

## Commands

```bash
cd ai-service
uv sync                                          # install deps
uv run uvicorn main:app --reload                 # dev server (port 8000)
uv run pytest                                    # all tests (no API keys needed)
uv run pytest tests/test_graph.py -k "test_name" # single test
uv run ruff check .                              # lint
uv run ruff format .                             # format
```

## Architecture

```
ai-service/
├── app/
│   ├── api/               # FastAPI endpoints
│   ├── graph/             # LangGraph core (state, nodes, graph, subgraphs)
│   ├── agents/            # Prompts + perfiles (base, doctor, crm...)
│   ├── orchestration/     # Router de intenciones del supervisor
│   ├── tools/             # Registry: schema + executor
│   ├── memory/            # Checkpointer (MemorySaver / PostgresSaver) + store
│   ├── rag/               # chunker, embeddings, vector store, retriever, ingest
│   ├── llm/               # Factory multi-proveedor (Anthropic/OpenAI) + costos
│   ├── safety/            # Guardrails (sanitización, prompt injection, rate limit)
│   └── core/              # config (pydantic-settings), logging, errors
├── tests/                 # pytest: graph, guardrails, rag, api
└── main.py
```

## Graph flow

```
START → guardrails → agent ⇄ tools → END
       │            (bind_tools + ToolNode)
       └─ inseguro → END (rechazo)
```

Subgraphs under `app/graph/subgraphs/` — specialized agents with isolated state.

## Key patterns

- **Tests sin API keys**: usan `FakeToolAwareModel` (wraps `FakeMessagesListChatModel`)
- **pytest-asyncio auto mode** — no `@pytest.mark.asyncio` needed
- **Ruff**: line-length 100, `B008` ignored (FastAPI `Depends()` in defaults is idiomatic)
- **Config via pydantic-settings**: `app/core/config.py` reads from `.env`
- **Multi-provider LLM**: Anthropic Claude / OpenAI via factory pattern
- **Checkpointer**: MemorySaver (dev) → PostgresSaver (prod)
- **Streaming SSE**: tokens + nodes en vivo via `/chat/stream`
- **RAG**: chunker propio + embeddings + vector store (pgvector en prod)

## Gotchas

- **`.env` required**: copy from `.env.example`, add `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`
- **PostgreSQL shared**: this service shares the backend's single Postgres (image `pgvector/pgvector:pg18` in the workspace root compose, port 5432, db `coppaddresd`, user `app_user` / `CoppAddresdDev!2026`). Do NOT create a second Postgres.
- **Schema `ai.` + Alembic**: all domain tables live in schema `ai.` (threads, messages, knowledge_chunks [pgvector 1536 + índice HNSW], agent_memories, agent_experiences, agent_feedback, agent_evaluations, agent_executions, agent_runtime_configs). The LangGraph PostgresSaver checkpointer also writes its tables (checkpoints, checkpoint_blobs, ...) into `ai.` (built with a psycopg connection and `options="-c search_path=ai,public"`). Alembic config is in `alembic/` (env.py filters autogenerate to schema `ai` only via `include_object`, so it NEVER touches backend schemas). Commands:
  ```bash
  uv run alembic upgrade head          # aplicar migraciones
  uv run alembic revision --autogenerate -m "desc"   # generar (solo schema ai)
  ```
  Models (SQLAlchemy async, `postgresql+psycopg`) live in `app/db/`. Migrations under `alembic/versions/` are excluded from ruff (`per-file-ignores`).
- **`langgraph.json`**: deployment config for LangGraph Platform
- **Comments/docs in Spanish** by convention

## Skills (MANDATORY before substantial work)

Load relevant skills from `.agents/skills/` before writing code:

| Tipo de trabajo | Skills a cargar |
|---|---|
| FastAPI endpoints | `fastapi-python` + `pydantic` + `python-executor` |
| Tests | `python-testing-patterns` + `testing` |
| RAG/embeddings | `machine-learning` + `pandas-data-analysis` |
| Templates | `fastapi-templates` |

## CodeGraph

When `.codegraph/` exists, use `codegraph_explore` FIRST for code understanding — one call returns verbatim source + call paths. Never grep/read chains when codegraph can answer in one call.
