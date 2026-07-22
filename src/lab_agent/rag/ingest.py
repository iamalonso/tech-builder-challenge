import logging
from pathlib import Path

from langchain_community.document_loaders import PyMuPDFLoader
from langchain_community.vectorstores import FAISS
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from lab_agent import config

logger = logging.getLogger(__name__)


def load_pdf_documents(pdf_dir: Path = config.PDF_DIR):
    docs = []
    for file in pdf_dir.glob("*.pdf"):
        try:
            loader = PyMuPDFLoader(str(file))
            docs.extend(loader.load())
            logger.info("Loaded PDF: %s", file.name)
        except Exception:
            logger.exception("Failed to load PDF: %s", file.name)
    return docs


def build_embedding_model() -> GoogleGenerativeAIEmbeddings:
    return GoogleGenerativeAIEmbeddings(
        model=config.EMBEDDING_MODEL,
        google_api_key=config.GEMINI_API_KEY,
    )


def build_vector_store(embedding_model: GoogleGenerativeAIEmbeddings) -> FAISS:
    """Builds the FAISS index from the PDFs, or loads it from disk if already persisted."""
    if (config.FAISS_INDEX_DIR / "index.faiss").exists():
        logger.info("Loading FAISS index from %s", config.FAISS_INDEX_DIR)
        return FAISS.load_local(
            str(config.FAISS_INDEX_DIR),
            embedding_model,
            allow_dangerous_deserialization=True,
        )

    docs = load_pdf_documents()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.CHUNK_SIZE,
        chunk_overlap=config.CHUNK_OVERLAP,
    )
    chunks = splitter.split_documents(docs)

    vector_store = FAISS.from_documents(documents=chunks, embedding=embedding_model)

    config.FAISS_INDEX_DIR.parent.mkdir(parents=True, exist_ok=True)
    vector_store.save_local(str(config.FAISS_INDEX_DIR))
    logger.info("Saved FAISS index to %s", config.FAISS_INDEX_DIR)

    return vector_store


def build_retriever():
    embedding_model = build_embedding_model()
    vector_store = build_vector_store(embedding_model)
    return vector_store.as_retriever(
        search_type="similarity_score_threshold",
        search_kwargs={
            "score_threshold": config.RETRIEVER_SCORE_THRESHOLD,
            "k": config.RETRIEVER_K,
        },
    )
