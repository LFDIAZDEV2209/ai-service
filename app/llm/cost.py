"""Seguimiento de tokens y estimación de costos por sesión (patrón del curso)."""

from dataclasses import dataclass

from langchain_core.messages import AIMessage

# Precio USD por millón de tokens (aproximado, actualizable):
#   [input, output]
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude": (3.0, 15.0),          # familia Claude Sonnet (aprox)
    "gpt-4o": (2.5, 10.0),          # familia GPT-4o (aprox)
    "default": (1.0, 3.0),          # fallback conservador
}


def _price_for(model: str) -> tuple[float, float]:
    model = (model or "").lower()
    for key, price in MODEL_PRICING.items():
        if key in model:
            return price
    return MODEL_PRICING["default"]


@dataclass
class SessionStats:
    """Acumula el uso de tokens de una sesión/thread."""

    model: str = "unknown"
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    turns: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, *, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

    def add_message(self, message: AIMessage) -> None:
        """Suma el uso reportado por LangChain en `usage_metadata` (si existe)."""
        usage = getattr(message, "usage_metadata", None) or {}
        self.add(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )

    def estimate_cost_usd(self) -> float:
        in_price, out_price = _price_for(self.model)
        return (
            self.input_tokens / 1_000_000 * in_price
            + self.output_tokens / 1_000_000 * out_price
        )

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "tool_calls": self.tool_calls,
            "turns": self.turns,
            "estimated_cost_usd": round(self.estimate_cost_usd(), 6),
        }
