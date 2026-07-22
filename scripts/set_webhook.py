"""Run once after deploying, to tell Telegram where to send updates.

Usage: python scripts/set_webhook.py
Requires TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_URL (and optionally
TELEGRAM_WEBHOOK_SECRET) set in the environment or .env file.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lab_agent.telegram.bot import set_webhook  # noqa: E402


if __name__ == "__main__":
    result = asyncio.run(set_webhook())
    print(result)
