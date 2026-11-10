"""
Tests for receipt extraction (Phase 4) and validation/normalization (Phase 5).

All Gemini calls are mocked.

Covers (per the plan Checkpoint 4):
  - Single-item receipt
  - 5-item receipt
  - 20+ item receipt
  - Handwritten/missing fields receipt
  - Mixed product categories
  - Missing fields (no date, no total, no unit_price)
  - Math mismatch (discount scenarios)
  - Unit normalization
  - Date parsing (multiple formats)
  - Indian comma notation in prices
  - Markdown-fenced JSON response
  - Trailing comma repair
  - Extract API endpoint (classify gate + extraction)
"""
import io
import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_jpeg_bytes(width: int = 400, height: int = 600) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(200, 200, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def _gemini_resp(payload: dict | str) -> MagicMock:
    m = MagicMock()
    m.text = json.dumps(payload) if isinstance(payload, dict) else payload
    return m


def _clf_accept_mock():
    """Returns a mock classifier response that accepts the image."""
    from app.services.classifier import ClassificationResult
    return ClassificationResult(
        is_receipt=True, confidence=0.97,
        document_type="supplier_purchase_receipt",
        reason="clear receipt", latency_ms=100.0,
        accepted=True, threshold_used=0.75,
    )


# ── Extractor unit tests ───────────────────────────────────────────────────────

class TestExtractor:

    @pytest.fixture(autouse=True)
    def reset_client(self):
        import app.services.extractor as ext
        original = ext._client
        ext._client = None
        yield
        ext._client = original

    @pytest.mark.asyncio
    async def test_single_item_receipt(self):
        from app.services.extractor import extract_receipt
        payload = {
            "supplier": "Sharma Traders",
            "date": "2026-09-29",
            "invoice_number": "ST-001",
            "receipt_total": 1850,
            "currency": "INR",
            "extraction_confidence": 0.97,
            "extraction_notes": None,
            "items": [
                {"raw_name": "Rice", "quantity": 25, "unit": "kg",
                 "unit_price": 74, "line_total": 1850},
            ],
        }
        with patch("app.services.extractor.genai.Client") as MockClient:
            MockClient.return_value.models.generate_content.return_value = _gemini_resp(payload)
            result = await extract_receipt(_make_jpeg_bytes(), "image/jpeg")

        assert result.supplier == "Sharma Traders"
        assert result.date == "2026-09-29"
        assert result.item_count == 1
        assert result.items[0].raw_name == "Rice"
        assert result.items[0].quantity == Decimal("25")
        assert result.items[0].unit == "kg"

    @pytest.mark.asyncio
    async def test_five_item_receipt(self):
        from app.services.extractor import extract_receipt
        items = [
            {"raw_name": f"Product {i}", "quantity": i, "unit": "pcs",
             "unit_price": 10, "line_total": i * 10}
            for i in range(1, 6)
        ]
        payload = {
            "supplier": "ABC Wholesalers", "date": None,
            "invoice_number": None, "receipt_total": sum(i*10 for i in range(1,6)),
            "currency": "INR", "extraction_confidence": 0.90,
            "extraction_notes": None, "items": items,
        }
        with patch("app.services.extractor.genai.Client") as MockClient:
            MockClient.return_value.models.generate_content.return_value = _gemini_resp(payload)
            result = await extract_receipt(_make_jpeg_bytes(), "image/jpeg")

        assert result.item_count == 5
        assert result.items[4].raw_name == "Product 5"

    @pytest.mark.asyncio
    async def test_twenty_plus_items(self):
        from app.services.extractor import extract_receipt
        items = [
            {"raw_name": f"Item {i:02d}", "quantity": 1, "unit": "kg",
             "unit_price": 50, "line_total": 50}
            for i in range(1, 22)
        ]
        payload = {
            "supplier": "Big Wholesaler", "date": "2026-09-01",
            "invoice_number": "BW-9999", "receipt_total": 50 * 21,
            "currency": "INR", "extraction_confidence": 0.88,
            "extraction_notes": "Large receipt", "items": items,
        }
        with patch("app.services.extractor.genai.Client") as MockClient:
            MockClient.return_value.models.generate_content.return_value = _gemini_resp(payload)
            result = await extract_receipt(_make_jpeg_bytes(), "image/jpeg")

        assert result.item_count == 21
        assert result.items[20].raw_name == "Item 21"

    @pytest.mark.asyncio
    async def test_handwritten_missing_fields(self):
        """Handwritten receipt with no supplier, no date, no totals."""
        from app.services.extractor import extract_receipt
        payload = {
            "supplier": None, "date": None,
            "invoice_number": None, "receipt_total": None,
            "currency": "INR", "extraction_confidence": 0.65,
            "extraction_notes": "Handwritten, some fields unreadable",
            "items": [
                {"raw_name": "Rice", "quantity": 10, "unit": "kg",
                 "unit_price": None, "line_total": 700},
                {"raw_name": "Soap", "quantity": 12, "unit": "pcs",
                 "unit_price": None, "line_total": 480},
            ],
        }
        with patch("app.services.extractor.genai.Client") as MockClient:
            MockClient.return_value.models.generate_content.return_value = _gemini_resp(payload)
            result = await extract_receipt(_make_jpeg_bytes(), "image/jpeg")

        assert result.supplier is None
        assert result.date is None
        assert result.item_count == 2
        assert result.extraction_confidence == pytest.approx(0.65)

    @pytest.mark.asyncio
    async def test_mixed_categories(self):
        """Receipt with food, cleaning, personal care items."""
        from app.services.extractor import extract_receipt
        items = [
            {"raw_name": "Rice",        "quantity": 25, "unit": "KG",   "unit_price": 74,  "line_total": 1850},
            {"raw_name": "PRLE G",      "quantity": 20, "unit": "PKT",  "unit_price": 10,  "line_total": 200},
            {"raw_name": "Harpic",      "quantity": 6,  "unit": "BTL",  "unit_price": 90,  "line_total": 540},
            {"raw_name": "Clinic Plus", "quantity": 8,  "unit": "PCS",  "unit_price": 90,  "line_total": 720},
            {"raw_name": "Sugar",       "quantity": 15, "unit": "KG",   "unit_price": 48,  "line_total": 720},
            {"raw_name": "Soap",        "quantity": 24, "unit": "PCS",  "unit_price": 40,  "line_total": 960},
        ]
        payload = {
            "supplier": "Sharma Traders", "date": "2026-09-29",
            "invoice_number": None, "receipt_total": 4990,
            "currency": "INR", "extraction_confidence": 0.95,
            "extraction_notes": None, "items": items,
        }
        with patch("app.services.extractor.genai.Client") as MockClient:
            MockClient.return_value.models.generate_content.return_value = _gemini_resp(payload)
            result = await extract_receipt(_make_jpeg_bytes(), "image/jpeg")

        assert result.item_count == 6
        names = [item.raw_name for item in result.items]
        assert "Rice" in names
        assert "PRLE G" in names
        assert "Harpic" in names

    @pytest.mark.asyncio
    async def test_indian_comma_notation_prices(self):
        """Prices like '1,850' should be parsed correctly."""
        from app.services.extractor import extract_receipt
        # Simulate model returning comma-formatted numbers as strings
        raw = json.dumps({
            "supplier": "Trader", "date": None, "invoice_number": None,
            "receipt_total": "1,850", "currency": "INR",
            "extraction_confidence": 0.9, "extraction_notes": None,
            "items": [
                {"raw_name": "Rice", "quantity": 25, "unit": "kg",
                 "unit_price": "74", "line_total": "1,850"},
            ],
        })
        mock_resp = MagicMock(); mock_resp.text = raw
        with patch("app.services.extractor.genai.Client") as MockClient:
            MockClient.return_value.models.generate_content.return_value = mock_resp
            result = await extract_receipt(_make_jpeg_bytes(), "image/jpeg")

        assert result.receipt_total == Decimal("1850")
        assert result.items[0].line_total == Decimal("1850")

    @pytest.mark.asyncio
    async def test_markdown_fenced_response(self):
        from app.services.extractor import extract_receipt
        payload = {
            "supplier": "Test", "date": "2026-09-30",
            "invoice_number": None, "receipt_total": 100,
            "currency": "INR", "extraction_confidence": 0.9,
            "extraction_notes": None,
            "items": [{"raw_name": "Sugar", "quantity": 2,
                        "unit": "kg", "unit_price": 50, "line_total": 100}],
        }
        fenced = f"```json\n{json.dumps(payload)}\n```"
        mock_resp = MagicMock(); mock_resp.text = fenced
        with patch("app.services.extractor.genai.Client") as MockClient:
            MockClient.return_value.models.generate_content.return_value = mock_resp
            result = await extract_receipt(_make_jpeg_bytes(), "image/jpeg")

        assert result.item_count == 1
        assert result.items[0].raw_name == "Sugar"

    @pytest.mark.asyncio
    async def test_trailing_comma_repair(self):
        """JSON with trailing commas should be repaired and parsed."""
        from app.services.extractor import extract_receipt
        broken_json = (
            '{"supplier": "Shop", "date": null, "invoice_number": null, '
            '"receipt_total": 200, "currency": "INR", "extraction_confidence": 0.9, '
            '"extraction_notes": null, '
            '"items": [{"raw_name": "Salt", "quantity": 2, "unit": "kg", '
            '"unit_price": 100, "line_total": 200,}],}'  # trailing commas
        )
        mock_resp = MagicMock(); mock_resp.text = broken_json
        with patch("app.services.extractor.genai.Client") as MockClient:
            MockClient.return_value.models.generate_content.return_value = mock_resp
            result = await extract_receipt(_make_jpeg_bytes(), "image/jpeg")

        assert result.item_count == 1

    @pytest.mark.asyncio
    async def test_raises_on_empty_response(self):
        from app.services.extractor import extract_receipt
        from app.exceptions import ExtractionError
        mock_resp = MagicMock(); mock_resp.text = ""
        with patch("app.services.extractor.genai.Client") as MockClient:
            MockClient.return_value.models.generate_content.return_value = mock_resp
            with pytest.raises(ExtractionError):
                await extract_receipt(_make_jpeg_bytes(), "image/jpeg")


# ── Normalizer / Validation unit tests ────────────────────────────────────────

class TestNormalizer:

    def _make_receipt(self, **kwargs):
        from app.schemas.extraction import ExtractedReceipt
        defaults = {
            "supplier": "sharma traders",
            "date": "29/09/2026",
            "items": [],
            "receipt_total": None,
            "extraction_confidence": 0.9,
        }
        defaults.update(kwargs)
        return ExtractedReceipt(**defaults)

    def _make_item(self, **kwargs):
        from app.schemas.extraction import ExtractedItem
        defaults = {
            "raw_name": "Rice",
            "quantity": Decimal("25"),
            "unit": "KG",
            "unit_price": Decimal("74"),
            "line_total": Decimal("1850"),
        }
        defaults.update(kwargs)
        return ExtractedItem(**defaults)

    def test_unit_normalisation(self):
        from app.services.normalizer import validate_and_normalise
        items = [
            self._make_item(raw_name="Rice",   unit="KG",  quantity=Decimal("1"), unit_price=Decimal("74"), line_total=Decimal("74")),
            self._make_item(raw_name="Biscuit", unit="PKT", quantity=Decimal("1"), unit_price=Decimal("10"), line_total=Decimal("10")),
            self._make_item(raw_name="Harpic",  unit="BTL", quantity=Decimal("1"), unit_price=Decimal("90"), line_total=Decimal("90")),
            self._make_item(raw_name="Shampoo",  unit="ML",  quantity=Decimal("1"), unit_price=Decimal("85"), line_total=Decimal("85")),
        ]
        receipt = self._make_receipt(items=items)
        result = validate_and_normalise(receipt)
        units = {item.raw_name: item.unit for item in result.receipt.items}
        assert units["Rice"] == "kg"
        assert units["Biscuit"] == "packet"
        assert units["Harpic"] == "bottle"
        assert units["Shampoo"] == "ml"

    def test_date_parsing_iso(self):
        from app.services.normalizer import validate_and_normalise
        from datetime import date
        receipt = self._make_receipt(date="2026-09-29")
        result = validate_and_normalise(receipt)
        assert result.receipt_date == date(2026, 9, 29)

    def test_date_parsing_dmy_slash(self):
        from app.services.normalizer import validate_and_normalise
        from datetime import date
        receipt = self._make_receipt(date="29/09/2026")
        result = validate_and_normalise(receipt)
        assert result.receipt_date == date(2026, 9, 29)

    def test_date_parsing_dmy_dash(self):
        from app.services.normalizer import validate_and_normalise
        from datetime import date
        receipt = self._make_receipt(date="29-09-2026")
        result = validate_and_normalise(receipt)
        assert result.receipt_date == date(2026, 9, 29)

    def test_missing_line_total_computed(self):
        """When line_total is missing but qty + unit_price present → compute it."""
        from app.services.normalizer import validate_and_normalise
        item = self._make_item(
            raw_name="Rice", quantity=Decimal("25"), unit="kg",
            unit_price=Decimal("74"), line_total=None,
        )
        receipt = self._make_receipt(items=[item])
        result = validate_and_normalise(receipt)
        assert result.receipt.items[0].line_total == Decimal("1850.00")
        assert result.item_validations[0].line_total_filled is True

    def test_missing_unit_price_computed(self):
        """When unit_price is missing but qty + line_total present → compute it."""
        from app.services.normalizer import validate_and_normalise
        item = self._make_item(
            raw_name="Rice", quantity=Decimal("25"), unit="kg",
            unit_price=None, line_total=Decimal("1850"),
        )
        receipt = self._make_receipt(items=[item])
        result = validate_and_normalise(receipt)
        assert result.receipt.items[0].unit_price == Decimal("74.00")
        assert result.item_validations[0].unit_price_filled is True

    def test_math_ok_within_tolerance(self):
        """Small rounding differences should still be math_ok."""
        from app.services.normalizer import validate_and_normalise
        # 25 × 74 = 1850, stated 1851 (₹1 rounding) — within ₹2 tolerance
        item = self._make_item(
            raw_name="Rice", quantity=Decimal("25"), unit="kg",
            unit_price=Decimal("74"), line_total=Decimal("1851"),
        )
        receipt = self._make_receipt(items=[item])
        result = validate_and_normalise(receipt)
        assert result.item_validations[0].math_ok is True

    def test_math_fails_on_large_discrepancy(self):
        """Large discrepancy (> 2%) should flag math failure."""
        from app.services.normalizer import validate_and_normalise
        # 25 × 74 = 1850, but stated 2000 — ₹150 off
        item = self._make_item(
            raw_name="Rice", quantity=Decimal("25"), unit="kg",
            unit_price=Decimal("74"), line_total=Decimal("2000"),
        )
        receipt = self._make_receipt(items=[item])
        result = validate_and_normalise(receipt)
        assert result.item_validations[0].math_ok is False
        assert result.item_math_failures == 1

    def test_total_match_within_tolerance(self):
        """5% tolerance on receipt total (covers discounts)."""
        from app.services.normalizer import validate_and_normalise
        items = [
            self._make_item(raw_name="Rice",  quantity=Decimal("25"), unit="kg",
                            unit_price=Decimal("74"), line_total=Decimal("1850")),
            self._make_item(raw_name="Soap",  quantity=Decimal("12"), unit="pcs",
                            unit_price=Decimal("40"), line_total=Decimal("480")),
        ]
        # Supplier gave ₹50 discount, stated total = 2280 instead of 2330
        receipt = self._make_receipt(items=items, receipt_total=Decimal("2280"))
        result = validate_and_normalise(receipt)
        assert result.total_match is True  # within 5%

    def test_total_mismatch_flagged(self):
        """Large total mismatch should be flagged."""
        from app.services.normalizer import validate_and_normalise
        items = [self._make_item(raw_name="Rice", quantity=Decimal("25"), unit="kg",
                                 unit_price=Decimal("74"), line_total=Decimal("1850"))]
        receipt = self._make_receipt(items=items, receipt_total=Decimal("500"))  # wildly off
        result = validate_and_normalise(receipt)
        assert result.total_match is False

    def test_supplier_name_titlecased(self):
        from app.services.normalizer import validate_and_normalise
        receipt = self._make_receipt(supplier="SHARMA TRADERS")
        result = validate_and_normalise(receipt)
        assert result.receipt.supplier == "Sharma Traders"

    def test_empty_items_still_valid(self):
        """Receipt with no items is not valid but shouldn't crash."""
        from app.services.normalizer import validate_and_normalise
        receipt = self._make_receipt(items=[])
        result = validate_and_normalise(receipt)
        assert result.is_valid is False


# ── Extract endpoint integration tests ────────────────────────────────────────

class TestExtractEndpoint:

    @pytest.fixture(autouse=True)
    def reset_clients(self):
        import app.services.extractor as ext
        import app.services.classifier as clf
        orig_ext = ext._client
        orig_clf = clf._client
        ext._client = None
        clf._client = None
        yield
        ext._client = orig_ext
        clf._client = orig_clf

    def _full_mock(self, clf_payload: dict, ext_payload: dict):
        """Returns two MagicMock responses for classifier and extractor."""
        clf_resp = MagicMock(); clf_resp.text = json.dumps(clf_payload)
        ext_resp = MagicMock(); ext_resp.text = json.dumps(ext_payload)
        return clf_resp, ext_resp

    def test_full_pipeline_success(self, client):
        import app.services.classifier as clf_mod
        import app.services.extractor as ext_mod
        from google import genai as _genai

        clf_payload = {
            "is_receipt": True, "confidence": 0.97,
            "document_type": "supplier_purchase_receipt",
            "reason": "Clear supplier bill.",
        }
        ext_payload = {
            "supplier": "Sharma Traders", "date": "2026-09-29",
            "invoice_number": "ST-001", "receipt_total": 1850,
            "currency": "INR", "extraction_confidence": 0.95,
            "extraction_notes": None,
            "items": [
                {"raw_name": "Rice", "quantity": 25, "unit": "kg",
                 "unit_price": 74, "line_total": 1850},
            ],
        }
        clf_resp = MagicMock(text=json.dumps(clf_payload))
        ext_resp = MagicMock(text=json.dumps(ext_payload))

        # Install pre-built mock clients directly into both modules
        clf_client = MagicMock()
        clf_client.models.generate_content.return_value = clf_resp
        ext_client = MagicMock()
        ext_client.models.generate_content.return_value = ext_resp

        clf_mod._client = clf_client
        ext_mod._client = ext_client

        response = client.post(
            "/api/v1/receipts/extract",
            files={"file": ("receipt.jpg", _make_jpeg_bytes(), "image/jpeg")},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["supplier"] == "Sharma Traders"
        assert body["item_count"] == 1
        assert body["items"][0]["raw_name"] == "Rice"
        assert body["is_valid"] is True


    def test_extract_rejects_non_receipt(self, client):
        clf_payload = {
            "is_receipt": False, "confidence": 0.02,
            "document_type": "not_a_receipt", "reason": "Photo of a dog.",
        }
        with patch("app.services.classifier.genai.Client") as MockClf:
            MockClf.return_value.models.generate_content.return_value = (
                MagicMock(text=json.dumps(clf_payload))
            )
            response = client.post(
                "/api/v1/receipts/extract",
                files={"file": ("dog.jpg", _make_jpeg_bytes(), "image/jpeg")},
            )

        assert response.status_code == 422
        assert response.json()["error"] == "NOT_A_RECEIPT"
