# Dependencias reproducibles de producción

`requirements-prod.txt` aplica `requirements-prod-constraints.txt` tanto en el
trabajo de tests de `.github/workflows/deploy-ai.yml` como en las imágenes de
`Dockerfile` y `Dockerfile.migrate`. Las 70 versiones provienen de la instalación
real del despliegue `73dbb59`, workflow `42255039816`, con Python 3.12. Son
metadatos públicos de paquetes, sin claves ni configuración del servicio.

El runtime contiene `langchain-anthropic 1.7.10`, `anthropic 2.6.0`,
`langchain-openai 1.7.4`, `openai 3.46.0`, `langchain-core 1.7.6` y
`pydantic 2.14.3`. El entorno histórico de `uv.lock` tenía versiones diferentes;
no se sobrescribió ni se actualizó el entorno local al corregir este problema.
Una actualización futura debe modificar las restricciones y volver a ejecutar
las pruebas con esa combinación antes de desplegar.

Las restricciones fijan una versión solo si se solicita ese paquete. No
convierten una dependencia específica de Linux en obligatoria para Windows o
macOS. Se comprobó la resolución de ruedas binarias para Linux x86_64/Python
3.12 y la instalación exacta de las 70 versiones en un entorno macOS/Python
3.12 aislado. La imagen de producción sigue siendo Linux/Python 3.12.

```bash
uv pip compile requirements-prod.txt --python-version 3.12 --python-platform x86_64-unknown-linux-gnu --only-binary=:all: --output-file /tmp/ai-linux-requirements.txt
```

La fábrica distingue tres intenciones: omitir el argumento usa la configuración
global, pasar un número lo conserva y pasar `None` omite el parámetro HTTP.
El registry no pasa ese argumento cuando el override sincronizado es null, para
conservar la herencia histórica del catálogo. No se cambian proveedores o
modelos ante el rechazo de parámetros del traductor.

En el SDK moderno de Anthropic, `Messages.create` dejó de declarar `temperature`
y LangChain la reenvía mediante `extra_body`. Por eso la mera construcción del
modelo y los tests con modelos falsos no detectaban el parámetro rechazado.
`tests/test_content_translation_provider_contract.py` atraviesa la fábrica, el
SDK instalado, HTTP simulado, el parser SSE y el endpoint: el contrato de error
capturado en el arreglo de narrativa `73dbb59` produce 503 con temperatura;
la omisión entrega una traducción validada con los mismos IDs y cantidades.
Se mantiene el error público genérico y no se registran textos privados.

La simulación comprueba la compatibilidad con ese contrato; la confirmación de
la incidencia productiva requiere releer los endpoints afectados después del
despliegue. No se confunde una respuesta HTTP simulada con una petición real al
proveedor. Las instrucciones del README permiten ejecutar la suite completa en
el mismo runtime sin tocar el entorno de desarrollo.
