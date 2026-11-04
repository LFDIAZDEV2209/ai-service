# Orientación práctica de síntomas

La guía `PRACTICAL_SYMPTOM_GUIDANCE` se inyecta por turno en `make_agent_node`,
incluso con perfiles personalizados sincronizados desde ERP. No hace falta
reactivar cada versión de agente. Reiniciar el servicio carga el código nuevo.

Ante síntomas leves ofrece medidas concretas de autocuidado y una indicación
breve de consultar si persisten, empeoran o se repiten. Puede explicar una opción
habitual sin receta por nombre genérico cuando hay contexto suficiente; antes
de sugerir su uso comprueba edad, embarazo cuando corresponda, alergias,
medicación ya tomada, alcohol y contraindicaciones. No considera la ausencia
de registros una historia negativa. No presenta cantidades, intervalos ni
máximos diarios: remite a la etiqueta local o al farmacéutico. No prescribe
fármacos de receta, diagnostica ni modifica tratamientos profesionales.

Las señales de alarma mantienen indicación inmediata de emergencias/SOS.
Una advertencia legal no sustituye las acciones útiles ni debe convertir toda
molestia transitoria en una derivación automática. La guía no agrega una lectura
automática del expediente clínico al chat: pide los datos faltantes.

Criterios consultados el 4 de noviembre de 2026:
- [NHS: headaches](https://www.nhs.uk/symptoms/headaches/)
- [MedlinePlus: acetaminophen](https://medlineplus.gov/druginfo/meds/a681004.html)
- [MedlinePlus: ibuprofen](https://medlineplus.gov/druginfo/meds/a682159.html)
- [FDA: acetaminophen](https://www.fda.gov/drugs/safe-use-over-counter-pain-relievers-and-fever-reducers/acetaminophen)

`antares-paciente/e2e/chat-symptom-guidance.mjs` recorre la app, Auth, Gateway,
API y este servicio con respuestas reales del proveedor. Identifica la burbuja
por el ID de la ejecución terminada, para no confundir una respuesta histórica
con el turno bajo prueba. Casos: cefalea leve/autocuidado, adulto con contexto
suficiente/opción OTC, cefalea súbita con déficit neurológico, y enfermedad
hepática con acetaminofén ya tomado. Los pacientes y mensajes son sintéticos;
no es una validación médica exhaustiva de las respuestas del modelo.

# Explicación de evaluaciones

`POST /internal/assessments/analyze`, protegido por la clave interna existente,
recibe hasta 250 resultados sin identificadores personales. Responde con texto
estructurado: resumen, 1–3 prioridades con evidencia y acción, ánimo y hasta
3 preguntas para el equipo. Usa el modelo configurado y valida que los códigos
de evidencia pertenezcan a la entrada. Devuelve 503 sin datos clínicos ni texto
del proveedor en logs si falla. No suma escalas ni asigna diagnósticos o rangos
no suministrados. La API .NET controla identidad, autorización, caché y persistencia.
