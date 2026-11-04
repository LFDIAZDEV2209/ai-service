# Narrativa de asignación del programa

`POST /internal/program/assignment-narrative` usa `X-Internal-Key` y recibe el snapshot clínico, la propuesta determinista y las fuentes autorizadas. Devuelve exclusivamente `narratives.es` y `narratives.en`; no elige ni modifica el contenido del programa. Los textos se validan por idioma, longitud y citas. El backend conserva el estado de generación y sus reintentos.

## Incidente 503 — 2026-11-04

Durante el E2E web del paciente sintético, el worker recibió 503. CloudWatch del ai-service mostró el error del proveedor: `temperature: Extra inputs are not permitted` (HTTP 400), convertido en 503 por la ruta interna.

`get_program_narrative_model()` forzaba `temperature=0.0`. La corrección copia únicamente la instancia `ChatAnthropic` de narrativa con `temperature=None`: LangChain omite ese campo del payload. No cambia la configuración ni las instancias de los agentes de chat; OpenAI conserva su comportamiento anterior. La propuesta y la validación de citas no cambian.

Prueba de regresión: inspección del payload real de `ChatAnthropic` sin llamadas de red, comprobando ausencia de `temperature` y preservación del modelo original. La suite de narrativa pasa 47/47; `uv run ruff check .` pasa.

Referencias: [ChatAnthropic](https://reference.langchain.com/python/langchain-anthropic/chat_models/ChatAnthropic), [migración de parámetros del proveedor](https://platform.claude.com/docs/en/docs/about-claude/model-deprecations). La causa del incidente se determinó a partir del error real, sin asumir un cambio de modelo por nombre.

El despliegue propuesto superpone solo `app/services/program_narrative.py` sobre el digest de producción de la revisión `cooppadresd-ai:30`. La aplicación y la reversión del rollout deben quedar registradas en `antares-paciente/docs/TEST-WEB-DEPLOY.md`; no se sustituyen secretos, dependencias, roles, recursos, ALB ni configuración de otros servicios.

El owner autorizó expresamente ese rollout: `cooppadresd-ai:31`, imagen `sha256:bcf51b44d1a06ac05bff10af48c37937c3ae7fcc413e54bc57e76d1dd4a58ce6`, estado `COMPLETED`, 1/1 tareas. También pasó la prueba del payload dentro de esa imagen. La verificación LIVE generó ES/EN reales: la recomendación `4fcba68a-ebf1-43e3-8771-688fb6456f51` pasó de `pending-narrative` a `proposed`, se aplicó por UI y el app completó una tarea con +120 XP y racha 1. Rollback disponible a la revisión 30; no se ejecutó.
