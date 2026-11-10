"""
Run the Discord adapter bot.

Usage:
    python run_bot.py
"""
import asyncio
import sys

from app.bot.client import ReceiptBot
from app.config import get_settings
from app.logging_config import configure_logging, get_logger

configure_logging()
log = get_logger("bot.main")


async def main():
    settings = get_settings()
    if not settings.discord_bot_token:
        log.error("DISCORD_BOT_TOKEN not configured. Set DISCORD_BOT_TOKEN in .env.")
        sys.exit(1)

    backend_url = f"http://{settings.host}:{settings.port}"
    bot = ReceiptBot(
        backend_url=backend_url,
        receipt_channel_id=settings.discord_receipt_channel_id or None,
        query_channel_id=settings.discord_query_channel_id or None,
    )

    log.info("starting_discord_bot", backend=backend_url)
    async with bot:
        await bot.start(settings.discord_bot_token)


if __name__ == "__main__":
    asyncio.run(main())
