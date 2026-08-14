# Adaptive memory — AI Service

Documentación del módulo `app/memory/adaptive.py` (fase 6 del plan de
agentes): el agente aprende de la experiencia de uso sin fine-tuning.

## Principio: separación de categorías

Nunca se mezclan en la misma tabla o vector store:

| Categoría | Dónde vive | Propiedad |
|---|---|---|
| Conocimiento oficial | `ai.knowledge_chunks` (RAG) | Del agente/institución |
| Memoria personalizada | `ai.agent_memories` (fase 5) | Del USUARIO/paciente |
| **Experiencia aprendida** | `ai.agent_experiences` (este módulo) | Del AGENTE |
| Datos del paciente | Tools de datos (backend) | Del paciente |
| Historial conversacional | Checkpointer + `ai.messages` | Del thread |

## Flujo

```
turno del chat
      │
      ▼
experience_load (nodo del grafo) ──► load_experiences → experience_context
      │                                  (bloque SEPARADO de memory_context)
      ▼
agent ⇄ tools
      │
      ▼
usuario da feedback (POST /api/v1/chat/feedback)
      │
      ▼
save_feedback (ai.agent_feedback) → save_experience (ai.agent_experiences)
                                     └─ upsert por trigger: recurrence_count++
                                        y success_rating promedio móvil
```

## Componentes

| Pieza | Descripción |
|---|---|
| `AdaptiveMemoryService.save_feedback` | Persiste rating 1-5 + comentario en `ai.agent_feedback`. |
| `AdaptiveMemoryService.save_experience` | Upsert por `trigger` normalizado: crea o refuerza el patrón (trigger → response). `outcome` = `success` (rating ≥ 4) o `error`. Promedio móvil del `success_rating` (pesa más lo reciente). |
| `AdaptiveMemoryService.load_experiences` | Solo experiencias `success` con recurrencia mínima (≥ 2), ordenadas por recurrencia × rating. El SQL filtra por agente + `_rank_experiences` re-filtra (defensa en profundidad). |
| `AdaptiveMemoryService.evaluate_response` | Evaluación heurística post-turno (0..1): respuesta no vacía, no replica el prompt, longitud razonable, uso de tools. |
| `app/graph/nodes.py` → `make_experience_load_node` | Inyecta `experience_context` (bloque separado de la memoria del usuario). |

## Endpoints

```
POST /api/v1/chat/feedback
{
  "thread_id": "...",          # obligatorio
  "rating": 5,                 # 1-5
  "comment": "opcional",
  "user_id": "opcional",
  "agent_type_id": "opcional", # si viene, se crea/refuerza la experiencia
  "trigger": "mensaje del usuario",
  "response": "respuesta evaluada"
}
```

Si faltan `agent_type_id`/`trigger`/`response`, solo se persiste el feedback
(sin experiencia). Errores de persistencia de experiencia se degradan con
gracia — el feedback siempre se guarda.

## Aislamiento

- La experiencia pertenece al **agente** (`agent_type_id`), nunca al usuario:
  es conocimiento aprendido agregado de uso.
- `load_experiences` y el SQL filtran SIEMPRE por `agent_type_id` — un agente
  nunca ve las experiencias de otro (verificado por tests y Postgres real).
- Las experiencias `error` y las de recurrencia baja no se inyectan (evitan
  ruido de un solo caso).

## Tests

```bash
uv run pytest tests/test_adaptive_memory.py -v   # 11 tests, sin BD ni API keys
```

Cubren: evaluación heurística, feedback → experiencia (éxito/error), upsert
con recurrencia y promedio móvil, selección de inyección (solo exitosas y
recurrentes, aisladas por agente), formato del bloque de contexto.
