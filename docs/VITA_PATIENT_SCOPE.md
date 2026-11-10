# Alcance de Vita para la primera publicación

El alcance acordado es **educación general en salud y ayuda para seguir planes
aprobados por el equipo**. Este documento describe controles de software y evidencia
pendiente; no es una declaración regulatoria ni sustituye la revisión de la empresa.

## Chat y explicaciones de evaluaciones

`PATIENT_SCOPE_POLICY` (`vita-patient-education-v1`) es una instrucción de sistema
propiedad del código. Se añade en cada invocación del grafo, también después de una
herramienta, con cualquiera de los cuatro perfiles y con configuraciones ERP.
Los perfiles editables, memoria y experiencia se incluyen como datos de menor
prioridad; los mensajes de sistema históricos se convierten en referencias. Un
prompt ERP que pida prescribir o ignorar límites no reemplaza esa instrucción.
La configuración sincronizada sigue determinando modelo, tema y herramientas.

Vita puede explicar conceptos, etiquetas e información de referencia sobre
medicamentos, hábitos cotidianos de bajo riesgo y hechos de un plan cuya aprobación
esté sustentada por una fuente disponible. No elige medicamentos para síntomas,
dosis personales, suplementos, objetivos clínicos, dietas terapéuticas nuevas ni
cambios del tratamiento. No interpreta laboratorios, signos vitales o escalas como
un diagnóstico, riesgo, mejoría, empeoramiento o garantía de seguridad. Un archivo,
recuerdo o afirmación del usuario no demuestra aprobación clínica.

Ante un posible peligro inmediato, conserva la orientación clara a contactar
servicios locales de emergencia/SOS. No afirma un diagnóstico, clasificación de
urgencia, atención ya enviada ni un número local inventado. Una consulta clínica
individual se remite al equipo, conservando educación útil dentro de este alcance.

El idioma claro del mensaje se conserva como preferencia de este turno; para
mensajes ambiguos se usa ES/EN de la app, sin persistir la detección. Esa selección
no modifica el alcance del paciente. Se conservan el routing por perfil y las
bases de conocimiento autorizadas incorporadas en dev.

El analizador de evaluaciones usa el mismo límite y conserva los metadatos de
instrumento, fecha, tipo y evaluaciones pendientes, así como un reintento acotado
para corregir el formato sin inventar resultados. Las etiquetas o severidades
recibidas se atribuyen a la fuente del cuestionario, sin confirmarlas como estado
clínico actual ni inventar fechas. Se conservan cuestionarios, puntuaciones y planes
registrados; no se recalculan ni eliminan. La API .NET debe invalidar su caché anterior
al desplegar el nuevo analizador para no servir narraciones creadas con el alcance viejo.

Las herramientas registradas del grafo incluyen hora, cálculo general, sugerencia
de una cita (CTA, sin reservar), señal de rechazo explícito de subida, consulta de
solo lectura del contexto del paciente autenticado y recuperación RAG acotada a las
bases asignadas. La consulta del paciente se habilita por perfil o selección explícita;
no se añade por defecto a todos los turnos. No hay una herramienta para prescribir
ni escribir tratamientos. Los resultados RAG son referencias, no nuevas instrucciones.
El subgrafo RAG de referencia también recibe el límite, aunque no esté conectado al
supervisor. La inyección proactiva no invoca modelos: los textos ya redactados del
backend conservan su propio proceso de revisión; su contexto histórico de sistema
no recupera prioridad en turnos posteriores.

Se conservan separados los endpoints profesionales de borradores nutricionales,
generación de planes y justificación de una propuesta determinista. Su resultado
sigue requiriendo el flujo de revisión profesional; este cambio no lo publica ni
convierte una propuesta en un plan aprobado. La traducción conserva el contenido
autorado y no añade una nueva evaluación clínica.

## Laboratorios: candidatos y hechos, sin interpretación autónoma

La extracción devuelve candidatos con valor/unidad tal como aparecen. No convierte
unidades ni completa la fecha con hoy, nacimiento o subida. `source_text` y
`source_page` son procedencia opcional no validada. Se ignora el resumen libre del
modelo y se usa una confirmación fija de candidatos pendientes de verificación.
La extracción sigue siendo probabilística: un prompt no demuestra exactitud OCR.

`POST /chat/lab-exam/narrate` conserva el campo `empathetic_message` pero ahora genera
texto factual ES/EN **sin LLM, BD ni persistencia**. Usa las adiciones del backend:

- `change`: `increase`, `decrease`, `unchanged`, `not_comparable`, `first_record`.
- `current_date`, `previous_source`, `current_source`, además de valores, unidades,
  fecha previa y delta ya existentes.
- `direction` antiguo se tolera, pero `improved`/`worsened` no autoriza un juicio
  clínico ni una comparación numérica sin el nuevo contrato verificable.

Solo narra aumentos/disminuciones cuando los valores, delta, fechas y unidades
coinciden; no convierte unidades ni repara tablas incompatibles. Sin comparabilidad,
conserva el valor actual y explica el límite. Procedencias `device`, `patient`,
`professional` son etiquetas de origen; `lab` es **extracción histórica**, no prueba
de verificación. Fuentes/unidades/nombres arbitrarios no se reproducen como órdenes.
`ocr_unverified` produce únicamente estado pendiente, sin afirmar cifras clínicas.
Los candidatos aislados de `metrics` tampoco se presentan como mediciones establecidas.

El despliegue coordinado de .NET debe mantener los nuevos OCR pendientes, con
`verificationStatus=pending_review` y `measurementCount=0`, sin guardar candidatos
como mediciones clínicas, completar tareas ni incorporarlos como hechos al chat.
El archivo original y la revisión profesional se conservan. Este repositorio no
demuestra por sí solo la configuración desplegada de ese flujo.

## Voz: versiones verificadas y cierre explícito

No se modifica por PATCH el idioma del agente compartido. Cada solicitud normaliza
ES/EN y selecciona una versión inmutable publicada. La configuración requerida es:

- `VOICE_ENABLED`, API key, agente y región existentes.
- `ELEVENLABS_PATIENT_VERSION_ES` y `ELEVENLABS_PATIENT_VERSION_EN`: IDs reales
  publicados `agtvrsn_…`, previamente revisados; no hay valores aprobados inventados.
- `ELEVENLABS_PATIENT_TOOL_CONFIG_SHA256`: JSON `{tool_id: huella_sha256}` aprobado
  de las definiciones completas de herramientas de citas, por defecto `{}`.

Antes de emitir cada credencial, el servicio lee la **misma versión exacta** con
GET, comprueba agente/version_id, prompt canónico exacto de
`build_patient_voice_prompt(language)`, idioma y saludo de `VOICE_FIRST_MESSAGES`.
También exige autenticación y rechaza overrides del cliente, presets de idioma,
KB/RAG, MCP nativo, herramientas inline/de servidor, procedimientos, workflows o
webhooks de inicio que sustituyan instrucciones. No se acepta un GET de versión
incompleto como prueba de conformidad. El signed URL se solicita pasando ese mismo
`version_id`; no hay caché compartida entre idiomas/versiones.

Los IDs de herramientas no constituyen aprobación: se resuelve cada uno vía GET en
cada sesión. Solo se permiten las ocho herramientas **client** de citas de la app
(`get_my_upcoming_appointments`, `get_my_requests`, `list_specialties`,
`list_professionals`, `get_availability_slots`, `request_appointment`,
`reschedule_appointment`, `cancel_appointment`). Su `tool_config` completo debe
coincidir con la huella aprobada; no se permiten respuestas simuladas. La identidad,
autorización, consentimiento, confirmación de escrituras y cancelación de sesión
continúan siendo responsabilidad de app/.NET. La respuesta .NET existente no cambia.

Para calcular una huella de un archivo JSON revisado, sin imprimir credenciales:

```python
from app.voice.client import patient_tool_config_digest
# tool_config es el objeto completo exportado y revisado; no estadísticas ni access_info.
print(patient_tool_config_digest(tool_config))
```

La serialización usa JSON UTF-8, claves ordenadas, separadores `,`/`:` y
`ensure_ascii=False`, sin NaN. Revisar semántica, descripciones y parámetros antes
de aprobar una huella; calcularla automáticamente no equivale a revisar el contenido.
Las versiones fijan la configuración de conversación, pero las definiciones de
herramientas y opciones de autenticación del proveedor siguen siendo mutables.
La comprobación por sesión detecta cambios antes de nuevas credenciales; no ofrece
una transacción atómica con el WebSocket ni detiene un cambio administrativo durante
una sesión activa. Hace falta restringir esos permisos y congelar/revisar cambios.

Si falta una versión, hay una discrepancia, no se puede verificar el proveedor o
cambia una herramienta, responde 503 con indisponibilidad amigable **sin emitir un
signed URL**. No hay fallback a un agente clínico o configuración mutable. Con
versiones sin herramientas, el mapa vacío permite solo la conversación educativa.

Referencias primarias de la integración:
[GET de versión](https://elevenlabs.io/docs/api-reference/agents/get),
[signed URL con version_id](https://elevenlabs.io/docs/api-reference/conversations/get-signed-url),
[versionado y límites](https://elevenlabs.io/docs/eleven-agents/operate/versioning),
[GET de definición de herramienta](https://elevenlabs.io/docs/api-reference/tools/get).

## Evidencia y pendientes de publicación

Las pruebas offline capturan mensajes reales que se entregan al adaptador falso,
verifican prioridad y reinyección, contratos numéricos, rutas profesionales intactas
y transportes HTTP simulados. Demuestran colocación de políticas y cierre de límites
de configuración; **no demuestran que un LLM nunca incumpla instrucciones**.

Quedan para el entorno autorizado: desplegar ambos servicios y caché revisionada,
crear/revisar las versiones ES/EN y herramientas, verificar la respuesta real de esos
endpoints en la región correcta, permisos y política del proveedor, y ejecutar casos
adversarios/texto/voz en ambos idiomas con datos sintéticos (dosis OTC personal,
interpretación de laboratorio, signos vitales, escala con severidad, prompt ERP
contradictorio, referencia educativa legítima, plan realmente aprobado y emergencia).
Registrar salida real, errores y revisión del equipo. No se ha llamado, modificado
ni aprobado un agente externo en esta tarea. La documentación oficial pendiente y
la determinación regulatoria de la empresa siguen siendo requisitos separados.
