# Historial mensual de Vita (AI)

Contrato compartido: `openspec/changes/vita-asistente-integral/HISTORIAL-MENSUAL.md`.
Este módulo administra historial/contexto conversacional local. No modifica planes,
resultados, documentos, conocimientos RAG ni registros clínicos.

## Contrato interno

`DELETE /api/v1/threads/{thread_id}/state?user_id={owner}` requiere
`X-Internal-Key`. BACK deriva el propietario del JWT de audiencia app; AI no acepta
autoridad del paciente en el body. No consulta catálogo ni consentimientos para
borrar, ni necesita instanciar un LLM. Responde 200 con:

```json
{
  "thread_id": "thread-publico",
  "history_revision": 1,
  "cleared_at": "2026-12-01T05:00:00Z",
  "reset_reason": "manual"
}
```

GET de la misma ruta conserva paginación y campos existentes y añade los tres
metadatos anteriores (ya devolvía `thread_id`). Inicialmente son `0/null/null`.
Un hilo limpiado devuelve 200 vacío, con metadatos, aunque ya no exista checkpoint.
Un hilo desconocido puede seguir devolviendo 404. El primer DELETE desconocido
crea un registro vacío con revisión 0; otra limpieza sin actividad conserva revisión,
fecha y motivo. Una actividad nueva permite incrementar la revisión otra vez.

Errores: 401 clave interna inválida, 422 identidad vacía/ambigua/demasiado larga,
409 hilo ocupado o admisión anterior a la limpieza, 503 borrado no confirmado o
persistencia indisponible. Nunca se transforma un fallo de borrado en 200.
El cliente reintenta tras 409/503 y consulta GET para obtener la revisión vigente.

## Registro opt-in y calendario

Solo se registran señales de BACK que identifican la app Vita:

- Chat: `supports_vita=true`, con propietario y sin canal `whatsapp`, incluso
  cuando el runtime Vita está desactivado y se usa el grafo legado.
- Voz: `supports_vita_voice=true`, al reservar el vínculo durable.
- Apertura GET: BACK puede enviar `supports_vita=true`; `sync_voice=true` ya es una
  señal explícita de opt-in y consentimientos verificados. Un GET sin señales no
  registra nuevos hilos. Una fila ya registrada siempre conserva su retención.
- DELETE: petición explícita del propietario por el proxy de la app.

No hay backfill por coincidencia de usuario/UUID ni inferencia desde playground,
WhatsApp o datos clínicos. Los hilos anteriores no identificados se incorporan al
recibir la primera señal confiable. BACK debe propagar `supports_vita` en apertura
para incorporar también pacientes que solo lean el chat.

`ai.vita_history` guarda clave compuesta propietario/hilo, mes procesado, revisión,
actividad y tombstone pendiente. El mes corresponde al primer día del mes calendario
en America/Bogota (UTC−05:00, sin DST), nunca a los últimos 30 días.
La apertura, admisión de turno, ejecución bajo lock, reserva e importación de voz
comprueban el mes. Un worker de lifespan procesa hasta 100 filas cada 30 segundos,
con timeout de 45 segundos por página. Un cursor evita que una primera página
ocupada impida procesar las demás. Los errores se reintentan en vueltas posteriores.
El barrido inicia también al arrancar el proceso: no depende de la app abierta ni de
un cron externo. La fila durable y el lock PostgreSQL coordinan réplicas/reinicios.
El corte lógico se aplica antes del siguiente acceso/turno; el borrado físico en
reposo tiene la latencia del barrido y su cola. Un turno/importación ya en curso
mantiene el lock: el DELETE devuelve 409 y el barrido lo reintenta al liberarse.

## Borrado y recuperación

Todas las operaciones de historial comparten `app.vita.locking.thread_guard`
con `LockedGraph` y el importador. La secuencia es:

1. Detectar checkpoint, proyección o actividad nueva bajo el lock del hilo.
2. Confirmar `pending_reset` y eliminar vínculos de voz del propietario/hilo;
   el FK cascade elimina recibos. Tokens/importaciones pierden su vínculo durable.
3. Ejecutar `checkpointer.adelete_thread(user_id + '::' + thread_id)`. El saver
   PostgreSQL elimina checkpoints, blobs y writes de todos los namespaces.
   Comprobar que `alist` ya no devuelve checkpoints.
4. Borrar `ai.messages` de la proyección UUID con propietario exacto, limpiar
   título/metadatos de `ai.threads` y confirmar revisión, mes y fin del tombstone.

El saver y SQLAlchemy usan conexiones distintas. El tombstone cubre un fallo o
caída entre commits: GET y turnos deben terminar el borrado antes de acceder al
estado; si no pueden, fallan sin exponer el checkpoint. El reintento conserva el
motivo/fecha originales e incrementa una sola vez, aunque el checkpoint ya hubiera
desaparecido antes del reinicio. Ninguna réplica puede limpiar a la vez que otra
escribe/importa usando este lock. La revisión capturada en admisión se valida de
nuevo dentro de `LockedGraph`; los requests anteriores a un DELETE no generan
mensajes, incluso si eran el primer turno y aún no había checkpoint.

Los resúmenes/memorias/experiencias antiguos no tienen hilo ni canal. Para Vita
opt-in los nodos no los leen/escriben y el inicio de turno vacía esos contextos.
No se borran esas tablas compartidas, lo que preserva otros canales y hechos
clínicos. El contexto conversacional Vita permanece en su checkpoint eliminable.
Observabilidad de chat opt-in redacta contenido también en modo legado.

El importador revalida cada vínculo dentro del mismo lock después de seleccionar
candidatos. Si una limpieza lo eliminó, ignora ese evento tardío sin consultar al
proveedor ni restaurar mensajes. Una reserva de voz nueva utiliza el mismo lock.

## Hook integrado por root en `vita_voice.py`

Root integró ambos puntos y los comprobó con HTTP ASGI + saver y recibos PostgreSQL
reales, además de cancelación y replay con revisión invalidada en pruebas aisladas:

1. Después de construir `config` y sus `voice_session_id`/IDs de mensajes, **antes**
   de la rama de replay de recibo completado y de `resolve_turn`, llamar:

   ```python
   await history_service.prepare_turn(
       record.user_id, record.thread_id, base_graph.checkpointer, config
   )
   ```

   Importar `history_service` de `app.history.service`. Mapear `VoiceContextError`
   con su `status` y `ThreadBusyError` a 409 (preparación está fuera del try actual).
   `prepare_turn` comprueba mes, vínculo activo y captura revisión. El grafo normal
   debe seguir siendo `LockedGraph`: ya ejecuta `before_turn_locked` sin otro lock.

2. El replay de recibo completado evita el grafo y requiere protección propia.
   Dentro del generador `replay()`, mantener `thread_guard` durante todos sus
   yields y ejecutar `await history_service.before_turn_locked(base_graph, config)`
   antes del primer yield. Puede mover ahí también la lectura de checkpoint.
   Así una limpieza entre lectura y envío no devuelve respuesta vieja. Una
   sesión eliminada/revisión cambiada interrumpe el replay sin emitir contenido.
   No envolver otra vez la ejecución normal: produciría un lock anidado.

BACK/APP deben terminar la llamada antes de DELETE e invalidar streams/peticiones
anteriores al limpiar o detectar revisión superior, incluso con GET vacío.
La integración local completa pasó 730 pruebas con PostgreSQL descartable, sin
omisiones (40 avisos heredados). Se añadieron después cuatro casos de herencia Global;
la pasada final y el recorrido publicado se registran en la entrega de root.

## Alcance de voz externa

El 200 confirma borrado **local** del checkpoint y proyecciones, así como pérdida
de vínculos/recibos que permitirían generación Custom LLM o importación tardía.
No certifica borrado de audio/transcripts almacenados por ElevenLabs: este módulo
no llama DELETE del proveedor ni mantiene una cola de borrado remoto. Aplican su
política de retención y la gestión del proveedor. No se restauran copias remotas
porque ya no existe el vínculo para importarlas.

La voz nativa legada usa una conexión directa app→ElevenLabs: eliminar el vínculo
local no puede cerrar por sí solo un WebSocket remoto ya abierto. APP debe cerrarlo
al limpiar/cambiar revisión; no se acredita aquí una terminación remota. Si se exige
revocación inmediata de generación independiente del cliente, root debe usar el
Custom LLM protegido o verificar un mecanismo de terminación del proveedor. No se
declara esa garantía para el transporte nativo con estas pruebas locales.

## Migración y pruebas

Revisión `18c4f6a91b20`, padre `fe62a09c1741`: crea solo `ai.vita_history` e índice,
sin backfill, seeds ni cambios en schemas del producto. Aplicar antes de iniciar
la nueva versión del servicio. Downgrade: detener entradas/worker nuevos y ejecutar
`uv run alembic downgrade fe62a09c1741`; elimina solo la tabla nueva. El esquema
es reversible; los mensajes eliminados a solicitud del propietario no se recuperan.

```powershell
uv run ruff check .
uv run pytest -q tests/test_vita_history.py
# VITA_TEST_DATABASE_URL debe venir del aprovisionamiento QA verificado.
uv run pytest -q tests/test_vita_history_postgres.py tests/test_vita_postgres.py
uv run pytest -q
```

Las pruebas PostgreSQL exigen host local y nombre de BD con `test`; crean schemas
únicos y los eliminan al cerrar. Las migraciones requieren una BD completamente
descartable: sus fixtures de baseline son sintéticos, no recrean tablas del producto.
Se ejecutaron con PostgreSQL temporal propio en `127.0.0.1:55449`, tras verificar
`SHOW data_directory`. El wrapper compartido de `55437` se detuvo por conexión
rechazada sin crear/borrar bases. No se inició el cluster compartido de QA.

Regresiones: propiedad/auth, GET vacío con revisión, idempotencia, checkpoint real
incluido namespace de subgrafo y tablas auxiliares, proyección propia, voz/recibos,
importación tardía/en curso, turnos/invocaciones/SSE anteriores al borrado, cambio
de mes/año/bisiesto, dos instancias, reinicio antes/después del borrado del saver,
fallo visible/reintentable y upgrade/downgrade/upgrade de la migración.
Los dobles de frontera de tests anteriores mantienen su ejecución sin BD; los
tests nuevos de historial ejercitan saver, sesiones SQL y locks reales.

Evidencia final de este carril:

- `uv run pytest -q` con la BD temporal verificada: **714 passed, 40 warnings in
  75.74s (0:01:15)**, sin skipped. Incluye 9 casos puros nuevos y 18 casos PostgreSQL
  nuevos; también las 2 integraciones PostgreSQL anteriores.
- `uv run ruff check .`: `All checks passed!`.
- `uv run ruff format --check` sobre los 16 archivos Python nuevos/modificados:
  `16 files already formatted`.
- `git diff --check`: sin salida, exit 0.
- `uv run alembic heads`: `18c4f6a91b20 (head)`.
- Avisos: deprecación Starlette/httpx, `FakeDbSession.add` asíncrono en fixtures
  anteriores y configuración `path_separator` de Alembic. No hubo fallos.

El CLI Postman instalado devuelve `unknown command 'status'`; no se creó ni publicó
un workspace/colección. El contrato ejecutable de este carril queda en FastAPI y
pytest. No se acredita aquí JWT público, UI física, tráfico real del proveedor ni
el hook protegido de root. Ninguna prueba usa pacientes o credenciales reales.
