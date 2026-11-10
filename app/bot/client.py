"""
Discord Bot Adapter for Receipt Intelligence System.

Adheres strictly to the architectural boundary in Section 21 of plan.md:
- Discord is a THIN ADAPTER, not part of the core business logic.
- ZERO database queries or domain logic live in this bot.
- All operations communicate over HTTP with the backend API.
"""
from __future__ import annotations

import io
from typing import Any

import discord
import httpx

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger(__name__)

SUPPORTED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".heic")


class ReceiptBot(discord.Client):
    """
    Thin Discord adapter client that forwards image uploads and queries
    to the FastAPI backend endpoints.
    """

    def __init__(
        self,
        backend_url: str = "http://localhost:8000",
        receipt_channel_id: int | None = None,
        query_channel_id: int | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        intents = kwargs.pop("intents", discord.Intents.default())
        intents.message_content = True
        super().__init__(intents=intents, *args, **kwargs)

        self.backend_url = backend_url.rstrip("/")
        self.receipt_channel_id = receipt_channel_id
        self.query_channel_id = query_channel_id
        self._http_client: httpx.AsyncClient | None = None

    async def get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or getattr(self._http_client, "is_closed", False) is True:
            self._http_client = httpx.AsyncClient(timeout=60.0)
        return self._http_client

    async def close(self) -> None:
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
        await super().close()

    async def on_ready(self) -> None:
        log.info("discord_bot_connected", user=str(self.user), backend=self.backend_url)

    async def on_message(self, message: discord.Message) -> None:
        # Ignore own messages or bot messages
        if message.author.bot or message.author == self.user:
            return

        channel_id = message.channel.id
        channel_name = getattr(message.channel, "name", "").lower()

        # ── Case 1: Receipt Upload Channel or Attachment ───────────────────────
        has_attachment = len(message.attachments) > 0
        is_receipt_channel = (
            (self.receipt_channel_id and channel_id == self.receipt_channel_id)
            or "receipt" in channel_name
            or has_attachment
        )

        if has_attachment and is_receipt_channel:
            await self.handle_receipt_upload(message)
            return

        # ── Case 2: Natural Language Query Channel ─────────────────────────────
        is_query_channel = (
            (self.query_channel_id and channel_id == self.query_channel_id)
            or "query" in channel_name
            or message.content.strip().startswith("!ask")
            or isinstance(message.channel, discord.DMChannel)
        )

        if is_query_channel and message.content.strip():
            await self.handle_user_query(message)
            return

    async def handle_receipt_upload(self, message: discord.Message) -> None:
        """Handle receipt image upload, send status, call backend, reply result."""
        attachment = message.attachments[0]
        filename = attachment.filename.lower()

        # Validate extension
        if not any(filename.endswith(ext) for ext in SUPPORTED_EXTENSIONS):
            await message.reply(
                "❌ Please upload a valid receipt image (JPEG, PNG, WebP, or HEIC)."
            )
            return

        status_msg = await message.reply("Receipt received ✓\nProcessing...")

        try:
            image_bytes = await attachment.read()
            client = await self.get_http_client()

            files = {"file": (attachment.filename, image_bytes, attachment.content_type or "image/jpeg")}
            response = await client.post(
                f"{self.backend_url}/api/v1/receipts/upload",
                files=files,
            )

            if response.status_code == 201:
                data = response.json()
                supplier = data.get("supplier_name") or "Unknown Supplier"
                item_count = data.get("item_count", 0)
                total = data.get("receipt_total")
                total_str = f"₹{total:,.2f}" if total is not None else "N/A"

                reply_text = (
                    f"✅ Receipt processed.\n\n"
                    f"**Supplier:** {supplier}\n"
                    f"**Items:** {item_count} items added\n"
                    f"**Total purchase:** {total_str}"
                )
                await status_msg.edit(content=reply_text)

            elif response.status_code == 422:
                err_data = response.json()
                if err_data.get("error") == "NOT_A_RECEIPT":
                    await status_msg.edit(
                        content=(
                            "❌ This doesn't appear to be a supplier receipt.\n"
                            "Please upload a clear receipt image."
                        )
                    )
                else:
                    await status_msg.edit(content=f"❌ Extraction error: {err_data.get('message', 'Failed to parse receipt.')}")

            elif response.status_code == 400:
                await status_msg.edit(
                    content="❌ The uploaded file is corrupt or not a recognized image format."
                )

            else:
                log.error("backend_receipt_upload_error", status_code=response.status_code, body=response.text)
                await status_msg.edit(content="⚠️ Failed to process receipt. Please try again.")

        except Exception as exc:
            log.error("discord_receipt_processing_exception", error=str(exc))
            await status_msg.edit(content="⚠️ An error occurred while communicating with the backend.")

    async def handle_user_query(self, message: discord.Message) -> None:
        """Handle natural language purchasing query via backend."""
        query_text = message.content.strip()
        if query_text.startswith("!ask"):
            query_text = query_text[4:].strip()

        if not query_text:
            return

        async with message.channel.typing():
            try:
                client = await self.get_http_client()
                response = await client.post(
                    f"{self.backend_url}/api/v1/queries",
                    json={"query": query_text},
                )

                if response.status_code == 200:
                    data = response.json()
                    answer = data.get("answer", "No response generated.")
                    await message.reply(answer)
                else:
                    await message.reply("⚠️ Sorry, I could not process your query at this time.")

            except Exception as exc:
                log.error("discord_query_processing_exception", error=str(exc))
                await message.reply("⚠️ An error occurred while contacting the intelligence backend.")
