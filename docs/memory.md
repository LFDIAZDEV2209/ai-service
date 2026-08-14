# Memoria de largo plazo del usuario — AI Service

Documentación del módulo `app/memory/` (fase 5 del plan de agentes): memoria
de largo plazo por usuario/paciente, aislada por agente.

## Arquitectura

```
chat (user_id, patient_id, agent_type_id, agent_instance_id)
        │
        ▼
memory_load (nodo del grafo) ──► UserMemoryService.load_context
        │                               │
        ▼                               ▼
agent ⇄ tools                  UserMemoryStore (ai.agent_memories)
        │
        ▼
memory_save (nodo del grafo) ──► extract_and_save (hechos)
                              └─► maybe_roll_summary (resumen rodante)
```

Los nodos `memory_load`/`memory_save` se insertan en el grafo solo cuando el
agente tiene `memory_config.enabled = true` (configuración versionada del
backend sincronizada en `ai.agent_runtime_configs`). El grafo base (sin
`agent_type_id`) no usa memoria.

## Componentes

| Archivo | Responsabilidad |
|---|---|
| `app/memory/extract.py` | Extracción heurística de hechos del mensaje del usuario (sin LLM, determinista): nombre, edad, preferencias, alergias/condiciones, objetivos. Categorías: `personal`, `preferencia`, `clinico`, `objetivo`, `resumen`. |
| `app/memory/postgres.py` | `UserMemoryStore`: persistencia en `ai.agent_memories` (SQLAlchemy async). Toda operación exige `user_id` explícito — no existen lecturas sin filtro de usuario. Deduplicación por clave y tope de 500 memorias/usuario. |
| `app/memory/service.py` | `UserMemoryService`: orquesta contexto (`load_context`), escritura (`extract_and_save`) y resumen rodante (`maybe_roll_summary`). El resumen usa un `summarizer` LLM inyectable con fallback determinista. |
| `app/graph/nodes.py` | `make_memory_load_node` / `make_memory_save_node`: nodos LangGraph que leen `configurable` (user_id, agent_instance_id) y el estado (`agent`). Errores de BD se degradan con gracia — la memoria nunca tumba el chat. |

## Aislamiento (regla de oro)

- Cada `load_context`/`extract_and_save` recibe `user_id` + `agent_type_id`
  explícitos; las queries filtran SIEMPRE por ambos.
- La memoria del agente de psicología de un paciente no es visible para el
  agente de nutrición del mismo paciente, ni para otro paciente.
- Verificado por tests: `test_aislamiento_entre_usuarios` y
  `test_aislamiento_entre_agentes_del_mismo_usuario`.

## Resumen rodante

- A partir de 6 mensajes en el historial del thread, `maybe_roll_summary`
  genera/actualiza un resumen de la conversación (categoría `resumen`).
- Usa un `summarizer` (LLM) si el agente lo provee; sin LLM, fallback
  determinista (primeros/últimos turnos concatenados, máximo 600 chars).
- El resumen es por (user_id, agent_type_id) — aislado entre usuarios.

## API

No hay endpoints públicos de memoria: el backend la gestiona indirectamente
vía chat (los nodos del grafo leen el `configurable` propagado por el backend
con `user_id`/`patient_id`/`agent_instance_id`).

## Tests

```bash
uv run pytest tests/test_memory.py -v   # 17 tests, sin BD ni API keys
```

Cubren: extracción (incl. dedupe y tildes), aislamiento entre usuarios y
agentes, categorías en contexto, persistencia de hechos, deduplicación y
resumen rodante (con/sin LLM).
