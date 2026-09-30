"""Módulo de voz conversacional (ElevenLabs Agents).

Responsabilidad: emitir sesiones temporales de voz para la app de pacientes.
El paciente NUNCA conoce la API key: el backend .NET pide la sesión aquí
(canal interno X-Internal-Key) y devuelve al cliente un signed URL de vida
corta. Toda la lógica de negocio (tools) la ejecuta la app con su propio JWT
contra endpoints del backend; ElevenLabs jamás accede a la base de datos.
"""
