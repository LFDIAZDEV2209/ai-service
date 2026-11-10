"""Idioma por turno: señales claras, ambigüedad y ausencia de persistencia."""

import pytest
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver

from app.agents.prompts import (
    PATIENT_SCOPE_POLICY,
    build_language_instruction,
    build_turn_policy,
)
from app.graph.graph import build_graph
from app.graph.language import detect_turn_language
from tests.fakes import RecordingFakeModel


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("How can I manage my anxiety?", "en"),
        ("¿Cómo puedo controlar mi ansiedad?", "es"),
        ("Necesito ayuda con mis emociones", "es"),
        ("Ánimo", "es"),
        ("A\u0301nimo", "es"),
        ("your vitals", "en"),
        ("mis medidas", "es"),
        ("hola", None),
        ("OK", None),
        ("123", None),
        ("", None),
        ("I feel ansiedad y necesito help", None),
        ("Please dime cómo estás", None),
        ("Thank you gracias", None),
    ],
)
def test_detect_turn_language(text, expected):
    assert detect_turn_language({"input": text}) == expected


@pytest.mark.parametrize(
    ("text", "app_language", "expected"),
    [
        ("How can I manage my anxiety?", "es", "en"),
        ("¿Cómo puedo controlar mi ansiedad?", "en", "es"),
        ("OK", "es", "es"),
        ("OK", "en", "en"),
        ("I feel ansiedad y necesito help", "es", "es"),
        ("I feel ansiedad y necesito help", "en", "en"),
        ("How can I manage my anxiety?", None, "en"),
    ],
)
async def test_graph_uses_detected_language_or_app_fallback(text, app_language, expected):
    fake = RecordingFakeModel(responses=[AIMessage(content="Respuesta")])
    graph = build_graph(model=fake, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "language", "language": app_language}}
    result = await graph.ainvoke({"input": text}, config=config)
    system = [msg.content for msg in fake.prompts[-1] if isinstance(msg, SystemMessage)]
    assert system[0] == PATIENT_SCOPE_POLICY
    assert build_language_instruction(expected) in system
    assert build_turn_policy(expected) in system
    assert build_language_instruction("es" if expected == "en" else "en") not in system
    assert "language" not in result
    assert "detected_language" not in result
    assert config["configurable"]["language"] == app_language


async def test_detection_does_not_persist_in_same_thread():
    fake = RecordingFakeModel(responses=[AIMessage(content="Respuesta") for _ in range(3)])
    graph = build_graph(model=fake, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "language-switch", "language": "es"}}
    for text, language in [("How can I manage anxiety?", "en"), ("OK", "es")]:
        await graph.ainvoke({"input": text}, config=config)
        blocks = [msg.content for msg in fake.prompts[-1] if isinstance(msg, SystemMessage)]
        assert build_language_instruction(language) in blocks
    snapshot = await graph.aget_state(config)
    assert "language" not in snapshot.values
    assert "detected_language" not in snapshot.values
