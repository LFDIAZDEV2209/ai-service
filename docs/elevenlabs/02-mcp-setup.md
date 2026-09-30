# 02 — Configuración del MCP de ElevenLabs en OpenCode

> **Documento Operativo — Fase 2 del plan `PROMPT_ELEVENLABS.md`**  
> **Estado:** Configurado y verificado por el orquestador (2026-09-29).

---

## 1. Datos Verificados del MCP y Workspace

El orquestador ha verificado la conexión operativa del **Hosted MCP Server oficial de ElevenLabs** con OpenCode:

- **Estado de conexión:** Conectado y autenticado activamente con rol **admin**.
- **Servidor MCP:** Hosted endpoint oficial para región US:
  ```text
  https://api.us.elevenlabs.io/v1/mcp
  ```
- **Recursos existentes en el Workspace:**
  El workspace cuenta actualmente con **1 agente configurado**:
  - **`agent_id`:** `agent_4501m3qqzq0ne7qtpcf3p2wkec1a`
  - **Nombre:** `Copp Adresd — Asistente Paciente (dev)`
  - **`voice_id`:** `cjVigY5qzO86Huf0OWal` (voz optimizada para Conversational AI)
  - **Tag:** `dev`
  - **Métrica de uso:** `0` llamadas en los últimos 7 días (listo para pruebas).
- **Herramientas (tools) MCP disponibles y operativas:**
  - `agents_list`: Listar agentes del workspace.
  - `agents_get`: Obtener la configuración detallada de un agente por ID.
  - `conversations_*`: Listar, consultar y depurar transcripciones de conversaciones.
  - `knowledge_base_*`: Listar, consultar y alimentar documentos en la base de conocimiento del agente.
  - `creative_generate_speech`: Generar pruebas directas de síntesis de voz (TTS).

---

## 2. Propósito y Delimitación de este Servidor MCP

> [!IMPORTANT]
> Es fundamental distinguir el rol de este MCP de desarrollo frente a la arquitectura de producción:
> - **Rol del MCP en OpenCode:** Permite a los agentes de desarrollo (OpenCode/Antigravity) inspeccionar, configurar, actualizar prompts y auditar el agente de ElevenLabs en la nube mediante lenguaje natural durante el ciclo de vida del software.
> - **Rol en Producción:** Los pacientes móviles **NO** utilizan este servidor MCP. En producción, la app móvil se conecta vía **WebRTC / WebSocket usando Signed URLs de corta duración** generadas por el backend, y el agente ejecuta **Tools de Negocio específicas** contra los servicios clínicos de CoppAdresd.

---

## 3. Guía de Operación: Verificación, Reconexión y Depuración

### 3.1 Cómo Verificar el Estado de Conexión

Para comprobar que OpenCode mantiene acceso válido al MCP de ElevenLabs, ejecuta desde la terminal:

```bash
opencode mcp list
```

**Resultado esperado:**
```text
elevenlabs: https://api.us.elevenlabs.io/v1/mcp (connected)
```

Para realizar una verificación funcional de lectura sin alterar recursos:
- En la interfaz de comandos o TUI de OpenCode:
  ```text
  /mcp call elevenlabs agents_list {"limit": 5}
  ```
  Debe retornar el listado JSON conteniendo el agente `Copp Adresd — Asistente Paciente (dev)`.

### 3.2 Procedimiento de Reconexión (Re-autenticación OAuth)

Si la sesión expira o el comando reporta `needs authentication`:

1. En la consola TUI de OpenCode, escribe el comando de barra:
   ```text
   /mcps
   ```
2. Selecciona `elevenlabs` de la lista interactiva.
3. Se abrirá automáticamente una pestaña en el navegador web solicitando el inicio de sesión en ElevenLabs.
4. Selecciona el workspace de CoppAdresd y presiona **Authorize**.
5. Regresa a OpenCode y valida la reconexión con `opencode mcp list`.

### 3.3 Diagnóstico y Depuración de Problemas Comunes

| Error / Síntoma | Causa Probable | Solución Verificada |
| :--- | :--- | :--- |
| `Protected resource https://api.us.elevenlabs.io/v1/mcp does not cover https://api.elevenlabs.io/v1/mcp` | Mismatch de URL regional. Se registró la URL global en lugar de la región US del workspace. | Volver a agregar el MCP especificando explícitamente `https://api.us.elevenlabs.io/v1/mcp`. |
| `Status 401 Unauthorized` al invocar tools MCP | Token OAuth expirado o revocado en el panel de ElevenLabs. | Ejecutar el procedimiento de reconexión (§3.2). |
| `Agent not found (404)` | El `agent_id` referenciado no pertenece al workspace autenticado. | Ejecutar `agents_list` para verificar los IDs vigentes en el workspace actual. |
| OpenCode no lista tools de ElevenLabs | El proceso MCP quedó suspendido o falló la negociación SSE. | Reiniciar la sesión de OpenCode o recargar con `opencode mcp restart elevenlabs`. |

---

## 4. Política Estricta de Seguridad: Qué NUNCA va a Git

> [!CAUTION]
> El incumplimiento de estas directrices de seguridad expone credenciales maestras y viola las normativas de seguridad del proyecto.

1. **Tokens OAuth y Credenciales MCP:**
   - OpenCode almacena las credenciales y tokens OAuth en el directorio de configuración del usuario del sistema operativo:
     ```text
     Windows: %USERPROFILE%\.config\opencode\
     Linux/macOS: ~/.config/opencode/
     ```
   - Estos archivos son locales por usuario y **NUNCA** deben copiarse al repositorio ni formar parte de `Repos/opencode.jsonc`.
2. **`ELEVENLABS_API_KEY`:**
   - La API key administrativa y de runtime **NUNCA** debe escribirse en:
     - Archivos versionados en Git (`.cs`, `.py`, `.ts`, `.tsx`, `.json`, `.yaml`).
     - Entornos cliente de la app móvil (`antares-paciente/.env`, variables `VITE_*`).
     - Dockerfiles ni imágenes de contenedor públicas.
     - Workflows de CI/CD (`.github/workflows/`).
   - La API key únicamente puede residir en:
     - **Entorno local de desarrollo:** Archivo `ai-service/.env` (estrictamente ignorado por `.gitignore`).
     - **Entornos Cloud (Dev / Staging / Prod):** **AWS Secrets Manager** (`coppaddresd/elevenlabs/api-key`), inyectada de forma segura en las variables de entorno de la tarea ECS de `ai-service`.
