# 01 — Auditoría de Arquitectura Actual (Previa a ElevenLabs)

> **Documento de Auditoría Técnica — Fases 0 y 1 del plan `PROMPT_ELEVENLABS.md`**  
> **Fecha de verificación:** 2026-09-29  
> **Estado:** Verificado contra el código fuente e infraestructura real del workspace (sin asunciones).

---

## 1. Resumen Ejecutivo del Ecosistema

La plataforma **CoppAdresd** opera como una solución integral de salud clínica (programa COPP-ADRESD) compuesta por una aplicación móvil para pacientes, un ERP administrativo para personal médico y directivo, un backend distribuido en .NET 10 con Clean Architecture, un servicio de inteligencia artificial basado en Python/LangGraph, un servicio de nutrición (`food-ai-service`), bases de datos compartidas pero aisladas por esquemas en PostgreSQL 16 con extensión pgvector, y caché distribuida en Valkey.

La incorporación de **ElevenLabs Conversational AI** tiene como meta habilitar un canal de voz bidireccional fluido, empático y seguro para la app del paciente, permitiendo consultas asistenciales, disponibilidad de agendas y gestión de citas sin que el proveedor de voz tenga acceso directo a las bases de datos ni reciba credenciales maestras.

---

## 2. Mapa de Componentes y Estado Real Verificado

| Componente | Repositorio / Directorio | Stack Tecnológico | Puertos Locales | Rol Actual y Relevancia para Voz |
| :--- | :--- | :--- | :--- | :--- |
| **App Pacientes** | `antares-paciente/` | React 19, Ionic 8.8, Capacitor 8, Vite, TypeScript | `:5173` | Cliente móvil donde se expone la interacción por voz. Cuenta con `VoiceOverlay.tsx` (maqueta visual) y gestión de sesión por JWT (`aud=app`). |
| **Gateway YARP** | `coppAddresdBack/src/Services/CoppAddresd.Gateway` | .NET 10, YARP (Reverse Proxy) | `:5080` | Punto de entrada unificado para clientes HTTP. Enruta tráfico a Auth, API, Telemedicine y Community. **No enruta `ai-service` directamente.** |
| **API Principal** | `coppAddresdBack/src/CoppAddresd.Api` | .NET 10, ASP.NET Core Minimal APIs / Controllers | `:5122` | Host monolito con lógica de pacientes, chat proxy (`ChatController`), auditoría y configuración general. Schema `app.`. |
| **Telemedicina & Citas** | `coppAddresdBack/src/Services/CoppAddresd.Telemedicine` | .NET 10, MediatR, EF Core 10, Twilio Video | `:5130` | Servicio standalone con dominio de citas (`appointments`), solicitudes (`telemedicine_requests`), agenda y salas virtuales. Schema `tele.`. |
| **Servicio de Auth** | `coppAddresdBack/src/Services/CoppAddresd.Auth` | .NET 10, ASP.NET Identity, Npgsql | `:5123` | Emisión y validación de tokens JWT (`aud=app` vs `aud=erp`), refresh tokens en cookies HttpOnly, OTP con Twilio Verify. Schema `auth.`. |
| **Servicio de IA** | `ai-service/` | Python 3.11+, FastAPI, LangGraph, psycopg async | `:8000` | Orquestación conversacional, checkpointer en PostgreSQL, RAG y multi-agente. Protegido por `X-Internal-Key`. Schema `ai.`. |
| **Servicio Food AI** | `food-ai-service/` | Python, FastAPI, PyTorch | `:8010` | Análisis de alimentos y macronutrientes. Independiente de `ai-service`. |
| **ERP / Panel Admin** | `coppaddresd-front/` | Next.js 16, React 19, Tailwind 4, Base UI | `:3000` | Consola administrativa clínica. Tokens JWT con audiencia `aud=erp`. |
| **Bases de Datos** | Raíz (`docker-compose.yaml`) | PostgreSQL 16 con `pgvector`, Valkey 7.2 | `:5432`, `:6379` | Instancia única de base de datos compartida por schemas (`auth`, `app`, `erp`, `tele`, `ai`, `audit`). Caché Valkey en `127.0.0.1:6379`. |

---

## 3. Auditoría Detallada de Subsistemas Relevantes para Voz

### 3.1 Frontend Móvil (`antares-paciente`)

- **Estado de `VoiceOverlay.tsx` (`src/components/VoiceOverlay.tsx`)**:
  - Actualmente es una **maqueta puramente cosmética** dentro de un `IonModal`.
  - Muestra textos estáticos fijos: *"Hola María, soy tu agente de salud Copp Adresd..."*, con controles de silenciar (`micOff`/`mic`) y finalizar llamada (`call`).
  - No captura audio mediante el micrófono, no procesa flujos de WebRTC ni transmite paquetes a ningún backend.
- **Iniciativa previa de voz en `openspec/changes/agente-asistente-citas`**:
  - Propuso un hook preliminar `useSpeechAssistant.ts` basado en la **Web Speech API** nativa (`window.webkitSpeechRecognition` + `window.speechSynthesis`).
  - *Limitaciones críticas identificadas en producción*: La Web Speech API presenta comportamiento dispar entre WebViews de iOS y Android, no permite interrupción natural del habla (barge-in), carece de una voz con identidad clínica de marca y no permite ejecutar herramientas estructuradas de forma bidireccional en tiempo real.
- **Manejo de Sesión y Autenticación**:
  - `src/context/AppContext.tsx` gestiona el estado global del usuario (`user`), tokens (`getAccessToken()`) y la navegación de pantallas basada en máquina de estados (`flow` y `screen`, sin `react-router` en uso activo).
  - Toda llamada al backend se realiza mediante `apiFetch` (`src/utils/apiClient.ts`), inyectando el Bearer token con `aud=app` y manejando refresco transparente de tokens ante respuestas 401.
- **Permisos de Audio y Hardware Nativo**:
  - `ios/App/App/Info.plist`: **Ya cuenta** con la clave requerida:
    ```xml
    <key>NSMicrophoneUsageDescription</key>
    <string>Copp Adresd usa el micrófono para las consultas de telemedicina.</string>
    ```
  - `android/`: Actualmente el proyecto cuenta únicamente con el scaffolding de `ios/` y web; el directorio `android/` no ha sido generado aún vía Capacitor CLI.
- **Módulo de Emergencia SOS (`PanicOverlay.tsx`)**:
  - Totalmente desacoplado de la voz conversacional ordinaria. Cuenta con confirmación deliberada y despacho SMS/FCM hacia `CoppAddresd.Api`.

---

### 3.2 Backend Monolito y Servicios (`coppAddresdBack`)

- **Ruta Actual de Chat e IA**:
  - `ChatController.cs` (`src/CoppAddresd.Api/Controllers/ChatController.cs`):
    - Expone `POST /api/v1/chat` y `POST /api/v1/chat/stream` (SSE con `text/event-stream`).
    - Exige autorización `[Authorize]`. La identidad del usuario se deriva estrictamente del token:
      ```csharp
      private string? AuthenticatedUserId => User.FindFirstValue(ClaimTypes.NameIdentifier);
      ```
    - Ningún endpoint permite inyectar el ID de paciente desde el cuerpo de la petición (protección anti-IDOR).
  - MediatR Handlers: `ChatCommandHandler.cs` y `StreamChatCommandHandler.cs` en `src/CoppAddresd.Application/Features/Chat/`.
- **Integración con `ai-service` (`AiServiceClient.cs`)**:
  - Ubicado en `src/CoppAddresd.Infrastructure/Services/AiServiceClient.cs`.
  - Configurado en `AiServiceSettings.cs`:
    - `BaseUrl`: `http://localhost:8000`
    - `ApiPrefix`: `/api/v1`
    - `InternalApiKey`: Clave secreta compartida transmitida en el encabezado HTTP `X-Internal-Key`.
  - Implementa resiliencia con Polly: 3 reintentos con backoff exponencial y circuit breaker ante 5 fallos consecutivos (30s break).
- **Módulo de Citas y Telemedicina (`CoppAddresd.Telemedicine`)**:
  - Es un microservicio standalone con base de datos en schema `tele.`.
  - Endpoints disponibles verificados:
    - `GET /api/v1/appointments/mine`: Citas del paciente autenticado derivadas del JWT (`CurrentUserId()`).
    - `GET /api/v1/appointments/availability`: Ranuras libres por fecha, especialidad o profesional en UTC.
    - `POST /api/v1/appointments`: Agendamiento directo con protección anti doble reserva mediante exclusión PostgreSQL GiST (`tstzrange`).
    - `POST /api/v1/appointments/{id}/reschedule`: Reprogramación transaccional de citas.
    - `POST /api/v1/appointments/{id}/cancel`: Cancelación con registro de motivo y auditoría.
    - `GET /api/v1/telemedicine/requests/mine`: Solicitudes del paciente.
    - `POST /api/v1/telemedicine/requests`: Creación de nueva solicitud de consulta.
    - `GET /api/v1/telemedicine/rooms/{id}`: Estado de la sala virtual Twilio Video.

---

### 3.3 Servicio de Inteligencia Artificial (`ai-service`)

- **Tecnología**: Python 3.11+, FastAPI, LangGraph, PostgreSQL async con psycopg 3.
- **Seguridad**:
  - Todos los endpoints funcionales están protegidos por la dependencia `require_internal_key` (`app/api/security.py`), que valida el encabezado `X-Internal-Key`.
  - No está expuesto directamente al público ni a través del Gateway.
- **Herramientas Actuales de Citas (`app/tools/appointment.py`)**:
  - Existe la tool `suggest_appointment`. **Hallazgo crítico:** Esta tool **no realiza acciones en base de datos** ni consulta citas; únicamente devuelve un JSON con `{ "type": "appointment", "cta_text": "...", "reason": "..." }` que luego el frontend interpreta como un botón CTA en la interfaz de chat.
- **Memoria y Checkpointing**:
  - Checkpointer en PostgreSQL (`ai.checkpoints`).
  - Hilo de conversación aislado por usuario mediante prefijo `storage_thread_id`: `{userId}::{threadId}`.

---

### 3.4 Gateway YARP (`CoppAddresd.Gateway`)

- Puerto `:5080`. Rutas configuradas en `appsettings.json`:
  - `/api/auth/{**catch-all}` → Auth Service (`:5123`, prioridad 100)
  - `/api/v1/telemedicine/appointments/{**catch-all}` y `/api/v1/appointments/{**catch-all}` → Telemedicine (`:5130`, prioridad 300/310)
  - `/api/v1/telemedicine/{**catch-all}` → Telemedicine (`:5130`, prioridad 300)
  - `/api/v1/community/{**catch-all}` y `/storage/{**catch-all}` → Community (`:5200`, prioridad 250/150)
  - `/api/v1/{**catch-all}` → API Monolito (`:5122`, prioridad 200)
- **Invariante arquitectónico**: Los clientes frontend consumen exclusivamente el Gateway en `:5080`. Ni `ai-service` ni `food-ai-service` tienen rutas en el Gateway; se consumen siempre tras el backend .NET.

---

## 4. Puntos de Integración para ElevenLabs

```
┌─────────────────────────────────────────────────────────────┐
│                 App Paciente (Ionic/Capacitor)              │
│       [VoiceOverlay con SDK ElevenLabs WebRTC/WebSocket]    │
└──────────────┬───────────────────────────────▲──────────────┘
               │ 1. POST /voice/session        │ 3. Audio bidireccional
               │    (Bearer JWT aud=app)       │    WebRTC (Signed URL)
               ▼                               │
┌──────────────────────────────┐               │
│        Gateway :5080         │               │
└──────────────┬───────────────┘               │
               ▼                               │
┌──────────────────────────────┐               │
│    CoppAddresd.Api :5122     │               │
│  (Valida JWT, Resuelve Sub)  │               │
└──────────────┬───────────────┘               │
               ▼ 2. POST /internal/voice/session
┌──────────────────────────────┐               │
│      ai-service :8000        │               │
│  (X-Internal-Key, SECRETS)   │               │
└──────────────┬───────────────┘               │
               │ Solicita Signed URL           │
               ▼                               │
┌──────────────────────────────────────────────┴──────────────┐
│                  ElevenLabs Conversational AI               │
│               (Agente con Tools Controladas)                │
└──────────────┬──────────────────────────────────────────────┘
               │ 4. Invocación de Tools de Negocio
               ▼
┌─────────────────────────────────────────────────────────────┐
│          Backend CoppAddresd (Application Services)         │
│  - AppointmentsController / Telemedicine                    │
│  - GetMyAppointmentsQuery / ScheduleAppointmentCommand      │
└─────────────────────────────────────────────────────────────┘
```

1. **Generación de Sesión Temporal de Voz**:
   - Nuevo endpoint en backend .NET: `POST /api/v1/chat/voice/session` (o similar), consumido por la app paciente con su JWT habitual.
   - Valida identidad, cuotas de rate limit y delega a `ai-service` (`POST /internal/voice/session` con `X-Internal-Key`).
   - `ai-service` mantiene la `ELEVENLABS_API_KEY` (obtenida de AWS Secrets Manager / `.env`) y solicita a ElevenLabs una **Signed URL de corta duración** para el agente dev o prod.
2. **Conexión de Audio en la App Móvil**:
   - La app recibe la `signed_url` y establece la conexión de audio por WebRTC/WebSocket utilizando la librería cliente oficial de ElevenLabs (`@elevenlabs/client` o `@elevenlabs/react`).
   - **La API key jamás viaja al cliente.**
3. **Ejecución de Tools de Negocio**:
   - Las tools del agente (`get_patient_upcoming_appointments`, `get_available_slots`, `request_appointment`, `cancel_appointment`, `reschedule_appointment`, `get_telemedicine_status`) no acceden a la base de datos.
   - En la modalidad **Client Tools**, la app móvil intercepta la solicitud de tool emitida por ElevenLabs y la ejecuta con su propio Bearer token contra los endpoints de Telemedicina existentes en `:5080`, devolviendo el JSON estructurado al agente para que continúe la conversación.

---

## 5. Matriz de Riesgos Identificados

| Riesgo | Nivel | Causa Raíz Potencial | Estrategia de Mitigación Obligatoria |
| :--- | :---: | :--- | :--- |
| **Exposición de `ELEVENLABS_API_KEY`** | **Crítico (P1)** | Filtración en variables de Vite (`VITE_*`), repositorios Git o paquetes compilados (APK/IPA). | La clave reside exclusivamente en AWS Secrets Manager y en el entorno de backend/ai-service. La app móvil solo recibe `signed_url` temporal de un solo uso. |
| **Suplantación de Paciente (IDOR en Tools)** | **Crítico (P1)** | Confiar en un `patient_id` textual inferido o emitido por el modelo de lenguaje de ElevenLabs. | La identidad del paciente se extrae **exclusivamente del claim `sub` del JWT** del paciente autenticado; los endpoints rechazan o ignoran IDs de paciente provenientes del payload de la tool. |
| **Tools destructivas o genéricas** | **Alto (P1)** | Crear tools estilo `execute_sql` o con permisos de escritura irrestrictos. | Prohibición estricta: Solo tools de negocio específicas con DTOs fuertemente tipados y validación MediatR / FluentValidation. |
| **Doble reserva concurrente de citas** | **Medio (P2)** | Paciente pide por voz una cita en un horario tomado simultáneamente en el ERP. | Reutilizar `ScheduleAppointmentCommand` que ya implementa la restricción de exclusión GiST de PostgreSQL (`tstzrange`), respondiendo conflicto controlado si la ranura se ocupó. |
| **Latencia excesiva en WebRTC** | **Medio (P2)** | Conexión con regiones distantes de ElevenLabs o re-encaminamiento innecesario de audio. | Uso de agentes en región US (`api.us.elevenlabs.io`) con modelo de voz ultrarrápido `eleven_flash_v2_5`. |
| **Privacidad de audio y transcripciones médicas** | **Alto (P1)** | Retención indefinida de voz y conversaciones médicas en la nube de ElevenLabs. | Configuración estricta de políticas de **Zero Retention** (`zero_retention_mode: true`) y borrado automático de audios y PII según FASE 10. |

---

## 6. Deuda Técnica Relacionada y Dependencias

1. **`VoiceOverlay.tsx` en `antares-paciente`**:
   - Requiere refactorización completa para reemplazar el mock visual por la máquina de estados del SDK de ElevenLabs (`connected`, `connecting`, `listening`, `speaking`, `error`, `muted`).
2. **Entorno Android en Capacitor**:
   - `antares-paciente` solo cuenta con `ios/`. Cuando se genere `android/`, se deberán declarar los permisos `RECORD_AUDIO` y `MODIFY_AUDIO_SETTINGS` en `AndroidManifest.xml`.
3. **Tools de Citas Desconectadas en `ai-service`**:
   - `ai-service` solo implementaba `suggest_appointment` sintética. Con ElevenLabs se requiere orquestar la integración con las verdaderas consultas y mutaciones de Telemedicina de `coppAddresdBack`.
4. **Almacén de Secretos Local vs Cloud**:
   - Localmente se debe proveer `ELEVENLABS_API_KEY` en `ai-service/.env` (ignorado en `.gitignore`). En AWS se debe inyectar mediante Secrets Manager en la task definition de ECS.
