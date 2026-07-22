import logging

from langchain_core.output_parsers import JsonOutputParser

from lab_agent.agent.prompts import (
    ASK_INFO_PROMPT,
    EVALUATE_PROMPT,
    OUT_OF_SCOPE_PROMPT,
    TRIAGE_PROMPT,
)
from lab_agent.agent.state import MedicalAgentState

logger = logging.getLogger(__name__)


def make_triage_node(llm):
    def triage_node(state: MedicalAgentState) -> MedicalAgentState:
        """Clasifica la consulta del usuario."""
        chain = TRIAGE_PROMPT | llm | JsonOutputParser()
        try:
            res = chain.invoke({"question": state["question"]})
            return {
                "triage_decision": res.get("decision", "FUERA_DE_ALCANCE"),
                "missing_fields": res.get("missing_fields"),
                "extracted_values": res.get("extracted_values", []),
            }
        except Exception:
            logger.exception("Triage node failed")
            return {
                "triage_decision": "FUERA_DE_ALCANCE",
                "missing_fields": None,
                "extracted_values": [],
            }

    return triage_node


def make_out_of_scope_node(llm):
    def out_of_scope_node(state: MedicalAgentState) -> MedicalAgentState:
        """Maneja consultas sobre síntomas o fuera del alcance de laboratorio."""
        response = (OUT_OF_SCOPE_PROMPT | llm).invoke({"question": state["question"]})
        return {
            "answer": response.content,
            "final_action": "out_of_scope",
        }

    return out_of_scope_node


def make_ask_info_node(llm):
    def ask_info_node(state: MedicalAgentState) -> MedicalAgentState:
        """Solicita los datos faltantes para poder evaluar el examen."""
        missing = state.get("missing_fields", "algunos datos complementarios")
        question = state["question"]

        response = (ASK_INFO_PROMPT | llm).invoke({"question": question, "missing": missing})
        return {
            "answer": response.content,
            "final_action": "ask_info",
        }

    return ask_info_node


def make_rag_node(retriever):
    def rag_node(state: MedicalAgentState) -> MedicalAgentState:
        """Consulta el índice FAISS para recuperar tablas y rangos de referencia del PDF."""
        extracted = state.get("extracted_values", [])
        query = " ".join(extracted) if extracted else state["question"]

        retrieved_docs = retriever.invoke(query)

        if retrieved_docs:
            rag_context_retrieved = "\n\n".join(doc.page_content for doc in retrieved_docs)
        else:
            rag_context_retrieved = "No se encontraron rangos de referencia para este parámetro en el documento cargado."

        return {"rag_context": rag_context_retrieved}

    return rag_node


def make_evaluate_node(llm):
    def evaluate_node(state: MedicalAgentState) -> MedicalAgentState:
        """Compara los valores del usuario contra el RAG y detecta si hay normalidad o valores críticos/de pánico."""
        question = state["question"]
        context = state.get("rag_context", "")

        response = (EVALUATE_PROMPT | llm).invoke({"question": question, "context": context})
        return {
            "answer": response.content,
            "final_action": "evaluated",
        }

    return evaluate_node


def route_triage(state: MedicalAgentState) -> str:
    """Determina hacia qué nodo avanzar según el resultado del triaje."""
    decision = state.get("triage_decision", "FUERA_DE_ALCANCE")

    if decision == "ANALIZAR_EXAMEN":
        return "rag_node"
    elif decision == "PEDIR_INFO":
        return "ask_info_node"
    else:
        return "out_of_scope_node"
