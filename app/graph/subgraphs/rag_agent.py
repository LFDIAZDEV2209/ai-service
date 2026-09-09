"""Subgrafo RAG — sub-agente con estado aislado (patrón de referencia).

Demuestra el patrón "subgraph como nodo con esquema de estado distinto":
el supervisor invoca este subgrafo dentro de un nodo, mapeando el estado
padre → estado hijo y devolviendo solo el resultado sintetizado al padre.
Así el sub-agente no contamina el historial de `messages` del supervisor.

TODO(lógica de negocio): conectar este subgrafo al supervisor cuando el RAG
tenga documentación real indexada.
"""

from __future__ import annotations

from typing import TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from app.llm.factory import get_chat_model
from app.rag.retriever import Retriever


class RagState(TypedDict, total=False):
    question: str
    context: str
    sources: list[str]
    answer: str


RAG_SYSTEM_PROMPT = (
    "Eres un asistente de documentación. Responde SOLO con la información del "
    "contexto proporcionado, citando la fuente de cada dato. Si la respuesta no "
    "está en el contexto, dilo claramente en lugar de inventar."
)


def _make_retrieve_node(retriever: Retriever):
    def retrieve(state: RagState) -> dict:
        results = retriever.retrieve(state["question"])
        return {
            "context": retriever.format_context(results),
            "sources": [r.chunk.metadata.get("source", "?") for r in results],
        }

    return retrieve


def _make_answer_node(model: BaseChatModel):
    def answer(state: RagState) -> dict:
        messages = [
            SystemMessage(content=RAG_SYSTEM_PROMPT),
            HumanMessage(
                content=(f"Contexto:\n{state.get('context', '')}\n\nPregunta: {state['question']}")
            ),
        ]
        response = model.invoke(messages)
        return {"answer": response.content}

    return answer


def build_rag_subgraph(
    *,
    model: BaseChatModel | None = None,
    retriever: Retriever | None = None,
    checkpointer=None,
):
    """Construye el subgrafo RAG (recuperar → responder)."""
    workflow = StateGraph(RagState)
    workflow.add_node("retrieve", _make_retrieve_node(retriever or Retriever()))
    workflow.add_node("answer", _make_answer_node(model or get_chat_model()))
    workflow.add_edge(START, "retrieve")
    workflow.add_edge("retrieve", "answer")
    workflow.add_edge("answer", END)
    return workflow.compile(checkpointer=checkpointer)


def make_rag_agent_node(
    *,
    model: BaseChatModel | None = None,
    retriever: Retriever | None = None,
):
    """Nodo para usar el subgrafo RAG DENTRO del supervisor (estado aislado).

    Recibe `state["input"]`, invoca el subgrafo y publica la respuesta como
    un AIMessage del supervisor más las fuentes en `rag_sources`.
    """
    subgraph = build_rag_subgraph(model=model, retriever=retriever)

    def rag_agent_node(state) -> dict:
        result = subgraph.invoke({"question": state.get("input", "")})
        return {
            "messages": [AIMessage(content=result.get("answer", ""))],
            "rag_sources": result.get("sources", []),
        }

    return rag_agent_node
