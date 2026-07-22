import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request

from lab_agent import config
from lab_agent.agent.graph import build_graph
from lab_agent.telegram.bot import _extract_answer_text, send_message

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

graph_app = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global graph_app
    logger.info("Building agent graph (this loads/creates the FAISS index)...")
    graph_app = build_graph()
    logger.info("Agent graph ready.")
    yield


app = FastAPI(title="Lab Agent API", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default=None),
):
    if config.TELEGRAM_WEBHOOK_SECRET:
        if x_telegram_bot_api_secret_token != config.TELEGRAM_WEBHOOK_SECRET:
            raise HTTPException(status_code=401, detail="Invalid secret token")

    update = await request.json()
    message = update.get("message") or update.get("edited_message")
    if not message or "text" not in message:
        return {"ok": True}

    chat_id = message["chat"]["id"]
    question = message["text"]

    try:
        result = graph_app.invoke({"question": question, "telegram_chat_id": chat_id})
        answer = result.get("answer", "No se pudo generar una respuesta.")
        answer_text = _extract_answer_text(answer)
    except Exception:
        logger.exception("Agent invocation failed")
        answer_text = "Ocurrió un error procesando tu consulta. Por favor intenta nuevamente."

    await send_message(chat_id, answer_text)
    return {"ok": True}
