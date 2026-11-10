"""
Tests for the receipt classifier (Checkpoint 3).

All Gemini calls are mocked — no real API key needed.
Tests cover:
  - Valid receipt image → accepted
  - Valid receipt image but low confidence → rejected by threshold
  - Non-receipt image → rejected
  - Image validator: too large, wrong format, corrupt bytes
  - JSON parsing: markdown-fenced response, missing fields
  - Gemini API failure
"""
import io
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image


# ── Fixtures: synthetic images ─────────────────────────────────────────────────

def _make_jpeg_bytes(width: int = 400, height: int = 600) -> bytes:
    """Create a minimal valid JPEG in memory."""
    buf = io.BytesIO()
    img = Image.new("RGB", (width, height), color=(200, 200, 200))
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_png_bytes(width: int = 400, height: int = 600) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (width, height), color=(180, 200, 220))
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Gemini mock helpers ────────────────────────────────────────────────────────

def _mock_gemini_response(payload: dict) -> MagicMock:
    """Return a mock that looks like a Gemini GenerateContentResponse."""
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(payload)
    mock_resp.candidates = [MagicMock(finish_reason="STOP")]
    return mock_resp


# ── Image validator tests ──────────────────────────────────────────────────────

class TestImageValidator:
    def test_valid_jpeg(self):
        from app.services.image_validator import validate_image
        info = validate_image(_make_jpeg_bytes(), filename="receipt.jpg")
        assert info.mime_type == "image/jpeg"
        assert info.width == 400
        assert info.height == 600

    def test_valid_png(self):
        from app.services.image_validator import validate_image
        info = validate_image(_make_png_bytes(), filename="receipt.png")
        assert info.mime_type == "image/png"

    def test_empty_file_raises(self):
        from app.services.image_validator import validate_image
        from app.exceptions import InvalidImageError
        with pytest.raises(InvalidImageError, match="empty"):
            validate_image(b"", filename="empty.jpg")

    def test_too_large_raises(self, monkeypatch):
        from app.services.image_validator import validate_image
        from app.exceptions import InvalidImageError
        from app.config import get_settings
        # Patch max upload to 100 bytes
        monkeypatch.setattr(get_settings(), "max_upload_bytes", 100)
        with pytest.raises(InvalidImageError, match="too large"):
            validate_image(_make_jpeg_bytes(), filename="big.jpg")

    def test_corrupt_bytes_raises(self):
        from app.services.image_validator import validate_image
        from app.exceptions import InvalidImageError
        with pytest.raises(InvalidImageError):
            validate_image(b"not an image at all", filename="fake.jpg")

    def test_tiny_image_raises(self):
        from app.services.image_validator import validate_image
        from app.exceptions import InvalidImageError
        buf = io.BytesIO()
        Image.new("RGB", (10, 10)).save(buf, format="JPEG")
        with pytest.raises(InvalidImageError, match="too small"):
            validate_image(buf.getvalue(), filename="tiny.jpg")


# ── Classifier unit tests ──────────────────────────────────────────────────────

class TestClassifier:

    @pytest.fixture(autouse=True)
    def reset_model(self):
        """Reset the lazy Gemini singleton between tests."""
        import app.services.classifier as clf_module
        original = clf_module._client
        clf_module._client = None
        yield
        clf_module._client = original

    @pytest.mark.asyncio
    async def test_accepts_high_confidence_receipt(self):
        from app.services.classifier import classify_receipt_image

        payload = {
            "is_receipt": True,
            "confidence": 0.97,
            "document_type": "supplier_purchase_receipt",
            "reason": "Clear supplier bill with items and prices.",
        }
        mock_resp = _mock_gemini_response(payload)

        with (
            patch("app.services.classifier.genai.Client") as MockClient,
        ):
            MockClient.return_value.models.generate_content.return_value = mock_resp
            result = await classify_receipt_image(_make_jpeg_bytes(), "image/jpeg")

        assert result.is_receipt is True
        assert result.confidence == pytest.approx(0.97)
        assert result.document_type == "supplier_purchase_receipt"
        assert result.accepted is True   # 0.97 >= default threshold 0.75

    @pytest.mark.asyncio
    async def test_rejects_low_confidence(self):
        from app.services.classifier import classify_receipt_image

        payload = {
            "is_receipt": True,
            "confidence": 0.50,   # below threshold
            "document_type": "unclear",
            "reason": "Blurry image, hard to tell.",
        }
        mock_resp = _mock_gemini_response(payload)

        with (
            patch("app.services.classifier.genai.Client") as MockClient,
        ):
            MockClient.return_value.models.generate_content.return_value = mock_resp
            result = await classify_receipt_image(_make_jpeg_bytes(), "image/jpeg")

        assert result.is_receipt is True
        assert result.accepted is False   # below threshold

    @pytest.mark.asyncio
    async def test_rejects_non_receipt(self):
        from app.services.classifier import classify_receipt_image

        payload = {
            "is_receipt": False,
            "confidence": 0.02,
            "document_type": "not_a_receipt",
            "reason": "Image is an OTP message screenshot.",
        }
        mock_resp = _mock_gemini_response(payload)

        with (
            patch("app.services.classifier.genai.Client") as MockClient,
        ):
            MockClient.return_value.models.generate_content.return_value = mock_resp
            result = await classify_receipt_image(_make_jpeg_bytes(), "image/jpeg")

        assert result.is_receipt is False
        assert result.accepted is False
        assert result.document_type == "not_a_receipt"

    @pytest.mark.asyncio
    async def test_parses_markdown_fenced_json(self):
        """Model sometimes wraps JSON in ```json ... ``` fences."""
        from app.services.classifier import classify_receipt_image

        fenced_text = (
            "```json\n"
            '{"is_receipt": true, "confidence": 0.92, '
            '"document_type": "supplier_purchase_receipt", '
            '"reason": "Clear bill."}\n'
            "```"
        )
        mock_resp = MagicMock()
        mock_resp.text = fenced_text
        mock_resp.candidates = [MagicMock(finish_reason="STOP")]

        with (
            patch("app.services.classifier.genai.Client") as MockClient,
        ):
            MockClient.return_value.models.generate_content.return_value = mock_resp
            result = await classify_receipt_image(_make_jpeg_bytes(), "image/jpeg")

        assert result.is_receipt is True
        assert result.accepted is True

    @pytest.mark.asyncio
    async def test_clamps_confidence_above_1(self):
        from app.services.classifier import classify_receipt_image

        payload = {
            "is_receipt": True,
            "confidence": 1.5,    # model hallucinated > 1
            "document_type": "supplier_purchase_receipt",
            "reason": "Very confident.",
        }
        mock_resp = _mock_gemini_response(payload)

        with (
            patch("app.services.classifier.genai.Client") as MockClient,
        ):
            MockClient.return_value.models.generate_content.return_value = mock_resp
            result = await classify_receipt_image(_make_jpeg_bytes(), "image/jpeg")

        assert result.confidence == pytest.approx(1.0)  # clamped

    @pytest.mark.asyncio
    async def test_raises_on_gemini_exception(self):
        from app.services.classifier import classify_receipt_image
        from app.exceptions import AIServiceError

        with (
            patch("app.services.classifier.genai.Client") as MockClient,
        ):
            MockClient.return_value.models.generate_content.side_effect = RuntimeError("API down")
            with pytest.raises(AIServiceError, match="Gemini Vision call failed"):
                await classify_receipt_image(_make_jpeg_bytes(), "image/jpeg")

    @pytest.mark.asyncio
    async def test_raises_on_unparseable_response(self):
        from app.services.classifier import classify_receipt_image
        from app.exceptions import AIServiceError

        mock_resp = MagicMock()
        mock_resp.text = "Sorry, I cannot help with that."
        mock_resp.candidates = [MagicMock(finish_reason="STOP")]

        with (
            patch("app.services.classifier.genai.Client") as MockClient,
        ):
            MockClient.return_value.models.generate_content.return_value = mock_resp
            with pytest.raises(AIServiceError, match="unparseable"):
                await classify_receipt_image(_make_jpeg_bytes(), "image/jpeg")

    @pytest.mark.asyncio
    async def test_raises_when_no_api_key(self, monkeypatch):
        from app.services.classifier import classify_receipt_image
        from app.exceptions import AIServiceError
        from app.config import get_settings
        monkeypatch.setattr(get_settings(), "gemini_api_key", "")
        with pytest.raises(AIServiceError, match="API key not configured"):
            await classify_receipt_image(_make_jpeg_bytes(), "image/jpeg")


# ── Classify API endpoint tests ────────────────────────────────────────────────

class TestClassifyEndpoint:
    """Integration-style tests for POST /api/v1/receipts/classify."""

    @pytest.fixture(autouse=True)
    def reset_model(self):
        import app.services.classifier as clf_module
        original = clf_module._client
        clf_module._client = None
        yield
        clf_module._client = original

    def test_classify_valid_receipt(self, client):
        payload = {
            "is_receipt": True,
            "confidence": 0.95,
            "document_type": "supplier_purchase_receipt",
            "reason": "Supplier bill with rice and soap.",
        }
        mock_resp = _mock_gemini_response(payload)

        with (
            patch("app.services.classifier.genai.Client") as MockClient,
        ):
            MockClient.return_value.models.generate_content.return_value = mock_resp
            response = client.post(
                "/api/v1/receipts/classify",
                files={"file": ("receipt.jpg", _make_jpeg_bytes(), "image/jpeg")},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["is_receipt"] is True
        assert body["accepted"] is True
        assert body["confidence"] == pytest.approx(0.95, abs=1e-3)
        assert "accepted" in body["message"].lower()

    def test_classify_non_receipt_returns_200_not_accepted(self, client):
        payload = {
            "is_receipt": False,
            "confidence": 0.03,
            "document_type": "not_a_receipt",
            "reason": "This is a photo of a dog.",
        }
        mock_resp = _mock_gemini_response(payload)

        with (
            patch("app.services.classifier.genai.Client") as MockClient,
        ):
            MockClient.return_value.models.generate_content.return_value = mock_resp
            response = client.post(
                "/api/v1/receipts/classify",
                files={"file": ("photo.jpg", _make_jpeg_bytes(), "image/jpeg")},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["is_receipt"] is False
        assert body["accepted"] is False

    def test_classify_no_file_returns_422(self, client):
        response = client.post("/api/v1/receipts/classify")
        assert response.status_code == 422

    def test_classify_corrupt_image_returns_400(self, client):
        response = client.post(
            "/api/v1/receipts/classify",
            files={"file": ("bad.jpg", b"not-an-image", "image/jpeg")},
        )
        assert response.status_code == 400
