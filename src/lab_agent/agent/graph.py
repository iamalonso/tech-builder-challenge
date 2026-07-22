from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, StateGraph

from lab_agent import config
from lab_agent.agent.nodes import (
    make_ask_info_node,
    make_evaluate_node,
    make_out_of_scope_node,
    make_rag_node,
    make_triage_node,
    route_triage,
)
from lab_agent.agent.state import MedicalAgentState
from lab_agent.rag.ingest import build_retriever


def build_llm() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=config.LLM_MODEL,
        temperature=0.0,
        google_api_key=config.GEMINI_API_KEY,
    )


def build_graph(llm: ChatGoogleGenerativeAI = None, retriever=None):
    llm = llm or build_llm()
    retriever = retriever or build_retriever()

    workflow = StateGraph(MedicalAgentState)

    workflow.add_node("triage_node", make_triage_node(llm))
    workflow.add_node("out_of_scope_node", make_out_of_scope_node(llm))
    workflow.add_node("ask_info_node", make_ask_info_node(llm))
    workflow.add_node("rag_node", make_rag_node(retriever))
    workflow.add_node("evaluate_node", make_evaluate_node(llm))

    workflow.set_entry_point("triage_node")

    workflow.add_conditional_edges(
        "triage_node",
        route_triage,
        {
            "rag_node": "rag_node",
            "ask_info_node": "ask_info_node",
            "out_of_scope_node": "out_of_scope_node",
        },
    )

    workflow.add_edge("rag_node", "evaluate_node")
    workflow.add_edge("evaluate_node", END)
    workflow.add_edge("ask_info_node", END)
    workflow.add_edge("out_of_scope_node", END)

    return workflow.compile()
