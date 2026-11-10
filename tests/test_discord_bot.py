"""
Tests for Phase 12 — Discord Bot Adapter (Checkpoint 8).

Verifies the thin adapter behavior without requiring a live Discord gateway connection:
  - Valid receipt upload (multi-item & handwritten)
  - Non-receipt rejection message
  - Invalid file extension rejection
  - Corrupt image error handling
  - Backend failure resilience
  - Natural-language query dispatch and response
  - Bot ignores its own or other bot messages
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bot.client import ReceiptBot


def _make_mock_attachment(filename="receipt.jpg", content=b"fake-image-bytes"):
    att = MagicMock()
    att.filename = filename
    att.content_type = "image/jpeg"
    att.read = AsyncMock(return_value=content)
    return att


def _make_mock_message(content="", author_is_bot=False, attachments=None, channel_name="receipts"):
    msg = MagicMock()
    msg.content = content
    msg.author = MagicMock()
    msg.author.bot = author_is_bot
    msg.channel = MagicMock()
    msg.channel.id = 12345
    msg.channel.name = channel_name
    msg.channel.typing = MagicMock()
    msg.channel.typing.return_value.__aenter__ = AsyncMock()
    msg.channel.typing.return_value.__aexit__ = AsyncMock()

    status_msg = MagicMock()
    status_msg.edit = AsyncMock()
    msg.reply = AsyncMock(return_value=status_msg)
    msg.attachments = attachments or []
    return msg, status_msg


class TestDiscordBotAdapter:

    @pytest.mark.asyncio
    async def test_bot_ignores_other_bots(self):
        bot = ReceiptBot(backend_url="http://testserver")
        msg, _ = _make_mock_message(author_is_bot=True)
        await bot.on_message(msg)
        msg.reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_extension_rejected_locally(self):
        bot = ReceiptBot(backend_url="http://testserver")
        att = _make_mock_attachment(filename="document.pdf")
        msg, _ = _make_mock_message(attachments=[att])

        await bot.handle_receipt_upload(msg)
        msg.reply.assert_awaited_once()
        args, _ = msg.reply.call_args
        assert "Please upload a valid receipt image" in args[0]

    @pytest.mark.asyncio
    async def test_receipt_upload_success(self):
        bot = ReceiptBot(backend_url="http://testserver")
        att = _make_mock_attachment(filename="receipt.jpg")
        msg, status_msg = _make_mock_message(attachments=[att])

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "supplier_name": "Sharma Traders",
            "item_count": 6,
            "receipt_total": 4280.0,
        }

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        bot.get_http_client = AsyncMock(return_value=mock_http)

        await bot.handle_receipt_upload(msg)

        msg.reply.assert_awaited_once_with("Receipt received ✓\nProcessing...")
        status_msg.edit.assert_awaited_once()
        content = status_msg.edit.call_args[1]["content"]
        assert "✅ Receipt processed." in content
        assert "Sharma Traders" in content
        assert "6 items added" in content
        assert "₹4,280.00" in content

    @pytest.mark.asyncio
    async def test_receipt_upload_rejected_as_non_receipt(self):
        bot = ReceiptBot(backend_url="http://testserver")
        att = _make_mock_attachment(filename="screenshot.png")
        msg, status_msg = _make_mock_message(attachments=[att])

        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.json.return_value = {"error": "NOT_A_RECEIPT", "message": "Not a receipt"}

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        bot.get_http_client = AsyncMock(return_value=mock_http)

        await bot.handle_receipt_upload(msg)

        status_msg.edit.assert_awaited_once()
        content = status_msg.edit.call_args[1]["content"]
        assert "❌ This doesn't appear to be a supplier receipt." in content

    @pytest.mark.asyncio
    async def test_receipt_upload_corrupt_image(self):
        bot = ReceiptBot(backend_url="http://testserver")
        att = _make_mock_attachment(filename="corrupt.jpg")
        msg, status_msg = _make_mock_message(attachments=[att])

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"error": "INVALID_IMAGE"}

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        bot.get_http_client = AsyncMock(return_value=mock_http)

        await bot.handle_receipt_upload(msg)

        status_msg.edit.assert_awaited_once()
        content = status_msg.edit.call_args[1]["content"]
        assert "corrupt or not a recognized image format" in content

    @pytest.mark.asyncio
    async def test_query_channel_success(self):
        bot = ReceiptBot(backend_url="http://testserver")
        msg, _ = _make_mock_message(content="How much Harpic did I buy in the last 3 months?", channel_name="queries")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "answer": "You bought 12 bottles of Harpic for a total of ₹1,080.00."
        }

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_resp)
        bot.get_http_client = AsyncMock(return_value=mock_http)

        await bot.handle_user_query(msg)

        msg.reply.assert_awaited_once_with(
            "You bought 12 bottles of Harpic for a total of ₹1,080.00."
        )

    @pytest.mark.asyncio
    async def test_query_channel_error_handling(self):
        bot = ReceiptBot(backend_url="http://testserver")
        msg, _ = _make_mock_message(content="!ask How much rice?", channel_name="general")

        mock_http = MagicMock()
        mock_http.post = AsyncMock(side_effect=Exception("Connection refused"))
        bot.get_http_client = AsyncMock(return_value=mock_http)

        await bot.handle_user_query(msg)

        msg.reply.assert_awaited_once()
        args, _ = msg.reply.call_args
        assert "⚠️ An error occurred" in args[0]
