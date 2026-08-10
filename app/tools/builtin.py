"""Herramientas base del agente.

Estas tools son genéricas y seguras. Las tools de negocio (datos de pacientes,
CRM, citas, etc.) se agregarán aquí o en nuevos módulos conforme salga la
lógica de negocio del backend .NET.
"""

import ast
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_core.tools import tool

from app.core.errors import ToolExecutionError


@tool
def get_current_time(timezone_name: str = "UTC") -> str:
    """Obtiene la fecha y hora actual en una zona horaria IANA.

    Args:
        timezone_name: zona horaria IANA, ej: "America/Mexico_City", "Europe/Madrid".
    """
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ToolExecutionError(
            f"Zona horaria desconocida: {timezone_name}. Usa nombres IANA válidos."
        ) from exc
    return datetime.now(tz).isoformat()


@tool
def calculate(expression: str) -> str:
    """Evalúa una expresión aritmética simple de forma segura.

    Soporta +, -, *, /, //, %, ** y paréntesis con números.

    Args:
        expression: expresión aritmética, ej: "(12 + 8) * 2".
    """
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Add, ast.Sub,
               ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.USub, ast.UAdd)
    try:
        tree = ast.parse(expression, mode="eval")
        for node in ast.walk(tree):
            if not isinstance(node, allowed):
                raise ValueError("operación no permitida")
            if isinstance(node, ast.Constant):
                # bool es subclase de int: rechazarlo explícitamente
                if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                    raise ValueError("solo se permiten números")
                if abs(node.value) > 1_000_000:
                    raise ValueError("número demasiado grande")
            if (
                isinstance(node, ast.Pow)
                and isinstance(node.right, ast.Constant)
                and abs(node.right.value) > 100
            ):
                raise ValueError("exponente demasiado grande")
        result = eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}}, {})
        if not isinstance(result, (int, float)) or abs(result) > 1e100:
            raise ValueError("resultado fuera de rango")
    except ToolExecutionError:
        raise
    except Exception as exc:
        raise ToolExecutionError(f"No se pudo evaluar la expresión: {exc}") from exc
    return str(result)
