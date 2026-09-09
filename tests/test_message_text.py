"""Tests de extract_message_text: el chat nunca debe mostrar el repr de Python
de los bloques de contenido de Claude ("[{'text': ...}]").
"""

from langchain_core.messages import AIMessage

from app.api.message_text import extract_message_text
from app.api.routes.chat import _extract_answer


def test_str_passthrough():
    assert extract_message_text("Hola") == "Hola"


def test_dict_blocks_exact_screenshot_case():
    # Caso real visto en el iPhone: lista de dicts con \n literales y
    # escapes \u200d — debe salir texto plano limpio.
    content = [
        {
            "text": "¡Hola! 👋 Soy CoppAI.\n\nEstoy aquí:\n- Información\n- Citas \u200d hoy\n",
            "type": "text",
            "index": 0,
        }
    ]
    out = extract_message_text(content)
    assert out.startswith("¡Hola! 👋 Soy CoppAI")
    assert "[{'text'" not in out
    assert "{'type'" not in out
    assert "- Información" in out
    assert "Citas \u200d hoy" in out


def test_tool_use_blocks_ignored():
    content = [
        {"type": "text", "text": "Te ayudo. "},
        {"type": "tool_use", "id": "x", "name": "calc", "input": {}},
    ]
    assert extract_message_text(content) == "Te ayudo. "


def test_object_blocks_with_text_attr():
    class Block:
        def __init__(self, text):
            self.text = text

    assert extract_message_text([Block("a"), Block("b")]) == "ab"


def test_none_and_empty():
    assert extract_message_text(None) == ""
    assert extract_message_text([]) == ""


def test_extract_answer_delegates_list():
    state = {"messages": [AIMessage(content=[{"type": "text", "text": "Hola de nuevo. 👋"}])]}
    assert _extract_answer(state) == "Hola de nuevo. 👋"


def test_extract_answer_str_unchanged():
    state = {"messages": [AIMessage(content="Plano")]}
    assert _extract_answer(state) == "Plano"
