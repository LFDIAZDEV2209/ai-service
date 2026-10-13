from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.graph.nodes import completed_tool_history


def test_preserves_completed_calculation_for_followup():
    question = HumanMessage(content="43 * 17")
    call = AIMessage(content="", tool_calls=[{"id": "c1", "name": "calculate", "args": {}}])
    tool = ToolMessage(content="731", tool_call_id="c1")
    answer = AIMessage(content="731")
    messages = [question, call, tool, answer]
    assert completed_tool_history(messages) == messages


def test_interrupted_tool_does_not_erase_previous_successful_turns():
    previous = [HumanMessage(content="Hola"), AIMessage(content="Hola")]
    interrupted = AIMessage(
        content="", tool_calls=[{"id": "missing", "name": "calculate", "args": {}}]
    )
    recovered = completed_tool_history([
        *previous, HumanMessage(content="Calcula"), interrupted,
        ToolMessage(content="unrelated", tool_call_id="other"),
    ])
    assert recovered == [*previous, HumanMessage(content="Calcula")]


def test_partial_multi_tool_batch_is_not_sent_to_provider():
    call = AIMessage(content="", tool_calls=[
        {"id": "a", "name": "calculate", "args": {}},
        {"id": "b", "name": "calculate", "args": {}},
    ])
    assert completed_tool_history([call, ToolMessage(content="1", tool_call_id="a")]) == []
