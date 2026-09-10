"""Ruta de extracción de métricas de exámenes de laboratorio (visión / texto).

Canal interno backend -> AI Service (requiere X-Internal-Key).
Extrae exclusivamente las 14 métricas del catálogo clínico soportado:
- glucose_fasting, hba1c, systolic_bp, diastolic_bp, heart_rate,
- o2_saturation, temperature_c, weight, height, bmi, body_fat,
- waist, hip, wrist.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError
from pypdf import PdfReader

from app.api.schemas import LabExamMetric, LabExamResponse
from app.api.security import require_internal_key
from app.core.errors import ConfigError
from app.llm.factory import get_chat_model
from app.rag.extract import DocumentExtractionError, extract_text

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/chat",
    tags=["lab-exam"],
    dependencies=[Depends(require_internal_key)],
)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB límite post-compresión

UNREADABLE_SUMMARY = (
    "The document appears unreadable. Please upload a clearer scan or a text-based PDF."
)
NO_METRICS_SUMMARY = "No lab values from the supported catalog were detected in this document."

SUPPORTED_METRIC_NAMES = {
    "glucose_fasting",
    "hba1c",
    "systolic_bp",
    "diastolic_bp",
    "heart_rate",
    "o2_saturation",
    "temperature_c",
    "weight",
    "height",
    "bmi",
    "body_fat",
    "waist",
    "hip",
    "wrist",
}

SYSTEM_PROMPT = """Eres un extractor especializado de datos clínicos de exámenes médicos.
Tu única función es extraer valores de laboratorio del catálogo de 14 métricas soportadas:

1. glucose_fasting: Glucosa / glicemia en ayunas (mg/dL, mmol/L)
2. hba1c: Hemoglobina glicosilada / A1c (%, mmol/mol)
3. systolic_bp: Presión arterial sistólica (mmHg)
4. diastolic_bp: Presión arterial diastólica (mmHg)
5. heart_rate: Frecuencia cardíaca / pulso (bpm, lpm)
6. o2_saturation: Saturación de oxígeno / SpO2 (%)
7. temperature_c: Temperatura corporal en Celsius (°C, C)
8. weight: Peso corporal (kg, lbs)
9. height: Estatura / talla (cm, m)
10. bmi: Índice de masa corporal / IMC (kg/m², índice)
11. body_fat: Porcentaje de grasa corporal (%)
12. waist: Perímetro / circunferencia de cintura (cm)
13. hip: Perímetro / circunferencia de cadera (cm)
14. wrist: Perímetro / circunferencia de muñeca (cm)

REGLAS ESTRICTAS:
1. Extrae SOLO las métricas del catálogo anterior presentes en el documento.
   Ignora cualquier otra prueba (hemograma, colesterol, orina, etc.).
2. Si el documento es ilegible, borroso, en blanco o corrupto, responde:
   {"readable": false, "summary": "The document appears unreadable.", "metrics": []}
3. Si el documento es legible pero no contiene métricas del catálogo, responde:
   {"readable": true, "summary": "No lab values detected.", "metrics": []}
4. Si contiene métricas del catálogo:
   - "metrics": lista con "metric_name", "value", "unit_symbol" y opcionalmente "observed_at".
   - "summary": resumen conciso mencionando ÚNICAMENTE el nombre de las métricas detectadas.
   - PROHIBIDO: NO incluyas diagnósticos, rangos de referencia ni consejos médicos en el resumen.
5. Devuelve ÚNICAMENTE un objeto JSON válido (sin fences de markdown ni texto adicional).
"""


def get_lab_exam_model() -> BaseChatModel:
    """Obtiene el modelo LLM para extracción de exámenes (inyectable para tests)."""
    try:
        return get_chat_model(
            temperature=0.0,
            max_tokens=2048,
        )
    except ConfigError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Servicio no configurado: {exc}",
        ) from exc


def _extract_json(text: str) -> dict[str, Any]:
    """Extrae el primer objeto JSON del texto tolerando fences de markdown y trailing commas."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:])
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start : end + 1]

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        stripped = re.sub(r",\s*([}\]])", r"\1", cleaned)
        return json.loads(stripped)


@router.post("/lab-exam", response_model=LabExamResponse)
async def extract_lab_exam(
    file: UploadFile = File(...),
    patient_id: str = Form(...),
    batch_id: str = Form(...),
    thread_id: str | None = Form(None),
    model: BaseChatModel = Depends(get_lab_exam_model),
) -> LabExamResponse:
    """Extrae métricas clínicas estructuradas de un archivo de examen de laboratorio."""
    content = await file.read()

    # Validar tamaño máximo (10 MB)
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File exceeds 10 MB limit")

    # Documento vacío
    if len(content) == 0:
        return LabExamResponse(
            readable=False,
            summary=UNREADABLE_SUMMARY,
            metrics=[],
        )

    filename = file.filename or "file.bin"
    content_type = file.content_type or ""

    is_pdf = (
        content_type == "application/pdf"
        or filename.lower().endswith(".pdf")
        or content.startswith(b"%PDF-")
    )

    messages = [SystemMessage(content=SYSTEM_PROMPT)]

    if is_pdf:
        pdf_text = ""
        try:
            pdf_text = extract_text(filename if filename.endswith(".pdf") else "exam.pdf", content)
        except DocumentExtractionError as exc:
            logger.info("PDF sin texto extraíble directo, intentando fallback de imágenes: %s", exc)

        if pdf_text and pdf_text.strip():
            messages.append(
                HumanMessage(
                    content=(f"Extrae los valores del catálogo del siguiente examen:\n\n{pdf_text}")
                )
            )
        else:
            # Fallback para PDFs escaneados: extraer imágenes de las páginas
            try:
                reader = PdfReader(io.BytesIO(content))
                images_found: list[dict[str, str]] = []
                for page in reader.pages:
                    for img in page.images:
                        b64 = base64.b64encode(img.data).decode("utf-8")
                        mime = "image/png" if img.name.lower().endswith(".png") else "image/jpeg"
                        images_found.append({"b64": b64, "mime": mime})

                if not images_found:
                    return LabExamResponse(
                        readable=False,
                        summary=UNREADABLE_SUMMARY,
                        metrics=[],
                    )

                content_list: list[dict[str, Any]] = [{
                    "type": "text",
                    "text": (
                        "Analiza las páginas escaneadas del examen y extrae las métricas del "
                        "catálogo."
                    ),
                }]
                for img_data in images_found[:3]:
                    content_list.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{img_data['mime']};base64,{img_data['b64']}"},
                    })
                messages.append(HumanMessage(content=content_list))
            except Exception as exc:
                logger.warning("Error extrayendo imágenes de PDF escaneado: %s", exc)
                return LabExamResponse(
                    readable=False,
                    summary=UNREADABLE_SUMMARY,
                    metrics=[],
                )
    else:
        # Imagen (JPEG, PNG, etc.)
        media_type = content_type if content_type.startswith("image/") else "image/jpeg"
        b64_img = base64.b64encode(content).decode("utf-8")
        messages.append(
            HumanMessage(
                content=[
                    {
                        "type": "text",
                        "text": "Analiza la imagen del examen y extrae las métricas del catálogo.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{media_type};base64,{b64_img}"},
                    },
                ]
            )
        )

    try:
        response = await model.ainvoke(messages)
        raw_content = response.content
        content_str = raw_content if isinstance(raw_content, str) else str(raw_content)
        parsed = _extract_json(content_str)
    except Exception as exc:
        logger.warning("Error al procesar el examen con el modelo LLM: %s", exc)
        return LabExamResponse(
            readable=False,
            summary=UNREADABLE_SUMMARY,
            metrics=[],
        )

    readable = bool(parsed.get("readable", True))
    if not readable:
        return LabExamResponse(
            readable=False,
            summary=parsed.get("summary") or UNREADABLE_SUMMARY,
            metrics=[],
        )

    raw_metrics = parsed.get("metrics", [])
    valid_metrics: list[LabExamMetric] = []
    for raw in raw_metrics:
        try:
            metric = LabExamMetric.model_validate(raw)
            if metric.metric_name in SUPPORTED_METRIC_NAMES:
                valid_metrics.append(metric)
        except ValidationError:
            continue

    if not valid_metrics:
        summary = NO_METRICS_SUMMARY
    else:
        summary = parsed.get("summary")
        if not summary or not summary.strip():
            names = ", ".join(m.metric_name for m in valid_metrics)
            summary = f"Se detectaron {len(valid_metrics)} métricas de laboratorio: {names}."

    return LabExamResponse(
        readable=True,
        summary=summary,
        metrics=valid_metrics,
    )
