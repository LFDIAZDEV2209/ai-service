# Educación sobre síntomas y resultados de cuestionarios

El alcance vigente se define en [VITA_PATIENT_SCOPE.md](VITA_PATIENT_SCOPE.md).
La antigua guía `PRACTICAL_SYMPTOM_GUIDANCE`, que permitía seleccionar una opción OTC
para síntomas individuales, se retiró. No debe reintroducirse mediante perfiles ERP,
memoria, documentación RAG ni configuración del proveedor de voz.

Vita conserva explicaciones generales útiles, hábitos cotidianos de bajo riesgo,
seguimiento del plan aprobado y orientación clara a contactar emergencias/SOS ante
peligro inmediato. No elige tratamiento/dosis personales ni interpreta el estado
clínico a partir de laboratorios, wearable o cuestionarios.

`POST /internal/assessments/analyze` conserva resumen, prioridades con evidencia,
ánimo y preguntas para el equipo. Sus etiquetas de origen se atribuyen al cuestionario;
no se convierten en diagnóstico, riesgo actual ni tratamiento independiente. La API
.NET mantiene identidad, autorización, persistencia y caché, que debe revisionarse
con el nuevo alcance.

El antiguo E2E `chat-symptom-guidance.mjs` contiene expectativas históricas de opciones
OTC personalizadas. Debe adaptarse al alcance aprobado antes de usarlo como criterio
de aceptación de esta versión; las pruebas de composición de este repositorio no
sustituyen validar las respuestas reales de chat y voz.
