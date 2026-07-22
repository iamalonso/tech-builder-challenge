import logging

import httpx

from lab_agent import config

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


def _extract_answer_text(answer) -> str:
    """The LLM response content can be a plain string or a list of content blocks."""
    if isinstance(answer, str):
        return answer
    if isinstance(answer, list):
        parts = [block.get("text", "") for block in answer if isinstance(block, dict)]
        return "\n".join(part for part in parts if part)
    return str(answer)


async def send_message(chat_id: int, text: str) -> None:
    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    url = f"{TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        )
        if response.status_code == 400:
            # El LLM puede generar markdown mal balanceado (asteriscos/guiones
            # bajos sin cerrar); Telegram rechaza el mensaje completo en ese
            # caso. Se reintenta en texto plano para no perder la respuesta.
            logger.warning(
                "Markdown parse failed, retrying as plain text: %s", response.text
            )
            response = await client.post(url, json={"chat_id": chat_id, "text": text})
        if response.status_code != 200:
            logger.error("Failed to send Telegram message: %s", response.text)
            response.raise_for_status()


async def set_webhook() -> dict:
    if not config.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    if not config.TELEGRAM_WEBHOOK_URL:
        raise RuntimeError("TELEGRAM_WEBHOOK_URL is not configured")

    url = f"{TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/setWebhook"
    payload = {"url": config.TELEGRAM_WEBHOOK_URL}
    if config.TELEGRAM_WEBHOOK_SECRET:
        payload["secret_token"] = config.TELEGRAM_WEBHOOK_SECRET

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.json()
