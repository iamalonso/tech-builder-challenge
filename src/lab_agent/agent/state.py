from typing import List, Optional, TypedDict


class MedicalAgentState(TypedDict, total=False):
    question: str                   # Pregunta original del usuario
    telegram_chat_id: Optional[int]  # ID de chat de Telegram
    triage_decision: str            # 'ANALIZAR_EXAMEN', 'PEDIR_INFO', 'FUERA_DE_ALCANCE'
    missing_fields: Optional[str]   # Datos faltantes si la información está incompleta
    extracted_values: List[str]     # Exámenes y valores detectados
    rag_context: str                # Rangos de referencia extraídos del PDF
    answer: Optional[str]           # Respuesta final estructurada
    final_action: str               # Nodo o ruta final ejecutada
