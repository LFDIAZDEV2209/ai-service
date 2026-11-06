# Traducción de contenido clínico publicado

`POST /internal/content/translate` traduce únicamente texto de presentación al
inglés. Usa `X-Internal-Key`, como los demás endpoints internos. El backend
autentica al paciente y selecciona campos permitidos; la app no llama a este
servicio directamente.

```json
{
  "target_language": "en",
  "items": [
    { "id": "meal-note", "text": "Tomar 1,5 mg a las 08:30." }
  ]
}
```

```json
{
  "target_language": "en",
  "items": [
    { "id": "meal-note", "translation": "Take 1,5 mg at 08:30." }
  ]
}
```

Los identificadores son opacos: 1–128 caracteres, únicos por lote. Se aceptan
1–64 elementos, textos no vacíos de hasta 8.000 caracteres y 32.000 caracteres
en total. Campos desconocidos, tipos incorrectos y otros idiomas se rechazan
con 422. No se normaliza el texto fuente. El resultado conserva todos los
identificadores y el orden del request.

El modelo existente de `app.llm.factory` recibe JSON separado del prompt del
sistema, sin herramientas. Sus identificadores internos son hashes del texto;
no se envían los IDs del llamador. El prompt ordena traducir el contenido
literalmente sin seguir instrucciones incrustadas ni añadir consejos,
diagnósticos, dosis o decisiones de aprobación.

Antes de invocar al modelo se protegen cantidades numéricas, unidades médicas,
códigos con números, enlaces, placeholders, etiquetas HTML y estructura básica
Markdown. El modelo debe conservar esos fragmentos una vez y en el mismo orden.
Después se comprueba que los fragmentos restaurados sean exactamente los de la
fuente. No se convierten unidades, fechas, decimales ni cantidades. Los nombres
de unidades domésticas escritos como palabras sí pueden traducirse, por ejemplo
`½ taza` a `½ cup`, conservando la cantidad. Se rechazan resultados incompletos,
IDs ajenos, cantidades nuevas y texto claramente español detectado por una
heurística acotada. Esta heurística no es un detector general de idioma ni una
prueba de equivalencia clínica de toda la prosa.

La política `clinical-presentation-en-v2` protege además dos equivalencias
revisadas ligadas a frases fuente exactas: `carbohidrato complejo` → `complex
carbohydrate` y `carbohidratos complejos` → `complex carbohydrates`. Estos términos
viajan como los mismos tokens protegidos y se restauran con su equivalencia,
conservando mayúsculas o capitalización inicial cuando corresponde. El modelo no
decide su traducción y no se sustituyen palabras de manera general después de
generar. Las comprobaciones de números, unidades y marcado mantienen su alcance previo;
las negaciones siguen dependiendo de la fidelidad del modelo y de su revisión; esta equivalencia puntual no prueba el
significado de otras frases. El cambio de versión invalida los hashes anteriores,
incluida una posible traducción errónea de estos términos.

La traducción es una proyección de lectura. Este endpoint no tiene acceso a
planes, pacientes ni aprobación y no modifica sus valores, fuentes o revisiones.
Una traducción automática no crea una nueva aprobación clínica. El backend debe
preferir traducciones revisadas ya almacenadas y conservar el texto original y
sus metadatos de aprobación como fuente de verdad.

Los textos duplicados se traducen una vez por lote. Solo un lote enteramente
válido entra en la cache LRU de 512 entradas y 24 horas, por
`SHA256(versión de política + idioma + texto fuente)`. Un cambio de texto
invalida la entrada por definición. La cache es local al proceso; el backend
puede añadir cache compartida o persistencia por el mismo hash para reutilizar
resultados entre réplicas y reinicios. No se registran textos, IDs del llamador,
salidas del modelo ni detalles privados de sus errores.

El servicio singleton comparte cuatro plazas de proveedor entre todas las
solicitudes. Divide los textos pendientes en grupos de hasta ocho elementos y
2.000 caracteres enmascarados; un texto individual mayor se conserva completo en
su propio grupo. Un único plazo de 28 segundos incluye la espera por una plaza,
todos los grupos y su validación, reservando margen antes del límite externo de
30 segundos. El primer fallo, la cancelación del cliente o el vencimiento cancelan
los grupos activos y pendientes. Ningún resultado nuevo entra en la cache hasta
que todos los grupos de la solicitud sean válidos; el contrato externo y la
fuente se conservan.

Un fallo de configuración, proveedor o timeout responde **503** con
`Translation unavailable`. Una respuesta
alterada o inválida responde **422** con `Translation output rejected`. No se
devuelve una traducción parcial ni se sustituye el resultado por español ante un
fallo. El backend debe propagar un estado de indisponibilidad recuperable; no
presentar el original como una traducción inglesa.

Verificación sin proveedor real:

```bash
uv run pytest tests/test_content_translation.py
uv run ruff check app/api/routes/content_translation.py app/schemas/content_translation.py app/services/content_translation.py tests/test_content_translation.py
```

Comprobación voluntaria con el proveedor real configurado, sin pacientes:

```bash
uv run python -m scripts.check_content_translation_nutrition --real-provider --items 56 --output /tmp/content-translation-56.json
uv run python -m scripts.check_content_translation_nutrition --real-provider --items 64 --output /tmp/content-translation-64.json
```

El script exige la opción explícita, usa exclusivamente la colección sintética
`tests/fixtures/content_translation_nutrition.json` y no cambia de proveedor ni
modelo. Invoca el router con su autenticación y validación, sin iniciar la base
de datos de la aplicación; mide la llamada real al proveedor y una repetición
con cache. Imprime solo métricas y permite guardar los textos sintéticos para
revisar su calidad. Comprueba los IDs, su orden, los fragmentos protegidos y que
solo se guarde el lote completo. No acredita la red del despliegue ni demuestra
por sí solo equivalencia clínica de toda la prosa.

En la comprobación del 6 de noviembre de 2026 con el modelo ya configurado
`claude-haiku-4-5-20251001`, el lote de 56 textos únicos (7.399 caracteres y 176
fragmentos protegidos) pasó de 503 en 30,268 s con una sola llamada a 200 en
15,414 s con grupos acotados. El lote máximo de 64 textos únicos (7.915 caracteres
y 195 fragmentos) devolvió 200 en 15,376 s y, tras proteger las equivalencias
revisadas de carbohidratos, en 16,755 s. Las repeticiones con cache fueron idénticas
(0,002 s y 0,001 s). Son observaciones de lotes sintéticos, no una
garantía de latencia para otros textos o condiciones de carga.

Una comprobación real adicional de cuatro frases con los términos revisados en
singular, plural, capitalización y mayúsculas, junto con negaciones y cantidades,
devolvió 200 en 2,088 s. Conservó `40 g`, `1,5 g` y `½`, restauró las cuatro
equivalencias esperadas y mantuvo en esas muestras las instrucciones de no
sustituir, no añadir azúcar, no cambiar la porción y no duplicar la cantidad.

El traductor construye el modelo con `temperature=None` explícito para omitir el
parámetro rechazado por los contratos recientes del proveedor. Conserva el
proveedor y el modelo configurados; no prueba otro proveedor ni recupera texto
español como éxito. La validación estructurada, los fragmentos protegidos, el
plazo total y la cache atómica mantienen su comportamiento. La combinación de
SDK usada por Docker/CI está fijada y se comprueba de forma aislada; véase
[dependencias de producción](production-dependencies.md).
