from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.graph.nodes import completed_tool_history


async def test_tool_result_reaches_model_without_repeating_user_request():
    from langgraph.checkpoint.memory import MemorySaver

    from app.graph.graph import build_graph
    from tests.fakes import RecordingFakeModel

    model = RecordingFakeModel(responses=[
        AIMessage(content="", tool_calls=[{
            "id": "calc-first", "name": "calculate", "args": {"expression": "43*17"},
        }]),
        AIMessage(content="731"),
        AIMessage(content="", tool_calls=[{
            "id": "calc-followup", "name": "calculate", "args": {"expression": "731+1"},
        }]),
        AIMessage(content="732"),
    ])
    graph = build_graph(model=model, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "tool-followup"}, "recursion_limit": 10}
    await graph.ainvoke({"input": "Calcula 43 * 17"}, config=config)
    result = await graph.ainvoke({"input": "Suma 1 al resultado anterior"}, config=config)

    # El modelo debe ver el resultado como último mensaje del ciclo, no otra petición.
    assert isinstance(model.prompts[1][-1], ToolMessage)
    assert model.prompts[1][-1].content == "731"
    assert isinstance(model.prompts[3][-1], ToolMessage)
    assert model.prompts[3][-1].content == "732"
    assert any(isinstance(message, AIMessage) and message.content == "731"
               for message in model.prompts[2])
    assert [message.content for message in result["messages"]
            if isinstance(message, HumanMessage)] == [
        "Calcula 43 * 17", "Suma 1 al resultado anterior",
    ]


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
