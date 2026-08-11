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
- **PostgreSQL separate**: this service uses its own Postgres (port 5432, creds: `coppai/coppai/db: coppai`) — don't mix with the .NET backend's Postgres
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
