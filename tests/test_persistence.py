"""
Tests for Phase 7 — Receipt Persistence and Retrieval.

Covers:
  - receipt_repo functions (create, get, list, save_items)
  - ingest_receipt_pipeline (full end-to-end processing & persistence)
  - POST /api/v1/receipts/upload
  - GET  /api/v1/receipts/{receipt_id}
  - GET  /api/v1/receipts
  - Rejection of invalid images and non-receipts
"""
import io
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from app.exceptions import InvalidImageError, NotAReceiptError
from app.models.purchase_item import PurchaseItem, ResolutionMethod
from app.models.receipt import Receipt, ReceiptStatus
from app.models.supplier import Supplier
from app.schemas.extraction import ExtractedItem, ExtractedReceipt
from app.schemas.receipt import ReceiptDetailResponse, ReceiptListResponse
from app.services.classifier import ClassificationResult
from app.services.ingestion import (
    ingest_receipt_pipeline,
    receipt_to_detail_response,
)
from app.services.resolver import ResolutionResult


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_jpeg_bytes(width: int = 400, height: int = 600) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(240, 240, 240)).save(buf, format="JPEG")
    return buf.getvalue()


def _make_dummy_receipt(supplier_name="Sharma Traders"):
    r_id = uuid.uuid4()
    s_id = uuid.uuid4()
    supplier = Supplier(id=s_id, name=supplier_name, normalized_name=supplier_name.lower())
    receipt = Receipt(
        id=r_id,
        supplier_id=s_id,
        supplier_raw_name=supplier_name,
        receipt_date=date(2026, 9, 29),
        invoice_number="INV-1001",
        receipt_total=Decimal("2570.00"),
        currency="INR",
        status=ReceiptStatus.stored,
        extraction_confidence=0.95,
        classifier_confidence=0.98,
        image_path="test_receipt.jpg",
        validation_notes="Totals matched",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    receipt.supplier = supplier

    item1 = PurchaseItem(
        id=uuid.uuid4(),
        receipt_id=r_id,
        raw_name="Rice",
        quantity=Decimal("25.0"),
        unit="kg",
        unit_price=Decimal("74.00"),
        line_total=Decimal("1850.00"),
        currency="INR",
        resolution_method=ResolutionMethod.normalized,
        resolution_confidence=0.95,
    )
    item2 = PurchaseItem(
        id=uuid.uuid4(),
        receipt_id=r_id,
        raw_name="Sugar",
        quantity=Decimal("15.0"),
        unit="kg",
        unit_price=Decimal("48.00"),
        line_total=Decimal("720.00"),
        currency="INR",
        resolution_method=ResolutionMethod.fuzzy,
        resolution_confidence=0.88,
    )
    receipt.purchase_items = [item1, item2]
    return receipt


# ── Receipt Repository Unit Tests ──────────────────────────────────────────────

class TestReceiptRepository:

    @pytest.mark.asyncio
    async def test_create_receipt(self):
        from app.repositories import receipt_repo

        mock_session = AsyncMock()
        mock_session.flush = AsyncMock()
        mock_session.add = MagicMock()

        receipt = await receipt_repo.create_receipt(
            mock_session,
            supplier_id=uuid.uuid4(),
            supplier_raw_name="Sharma Traders",
            receipt_date=date(2026, 9, 29),
            invoice_number="INV-1234",
            receipt_total=Decimal("2000.00"),
            currency="INR",
            status=ReceiptStatus.stored,
            extraction_confidence=0.94,
            classifier_confidence=0.99,
        )

        assert receipt.supplier_raw_name == "Sharma Traders"
        assert receipt.invoice_number == "INV-1234"
        assert receipt.status == ReceiptStatus.stored
        mock_session.add.assert_called_once_with(receipt)
        mock_session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_receipt_by_id(self):
        from app.repositories import receipt_repo

        dummy = _make_dummy_receipt()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = dummy
        mock_session.execute = AsyncMock(return_value=mock_result)

        res = await receipt_repo.get_receipt_by_id(mock_session, dummy.id)
        assert res is not None
        assert res.id == dummy.id
        assert len(res.purchase_items) == 2
        assert res.supplier.name == "Sharma Traders"

    @pytest.mark.asyncio
    async def test_list_receipts_with_filters(self):
        from app.repositories import receipt_repo

        dummy = _make_dummy_receipt()
        mock_session = AsyncMock()

        # Mock count query result
        count_res = MagicMock()
        count_res.scalar_one.return_value = 1

        # Mock data query result
        data_res = MagicMock()
        data_res.scalars.return_value.all.return_value = [dummy]

        mock_session.execute = AsyncMock(side_effect=[count_res, data_res])

        receipts, total = await receipt_repo.list_receipts(
            mock_session,
            date_from=date(2026, 9, 1),
            date_to=date(2026, 9, 30),
            limit=10,
            offset=0,
        )

        assert total == 1
        assert len(receipts) == 1
        assert receipts[0].id == dummy.id

    @pytest.mark.asyncio
    async def test_save_purchase_items(self):
        from app.repositories import receipt_repo

        mock_session = AsyncMock()
        mock_session.add_all = MagicMock()
        mock_session.flush = AsyncMock()

        items = [
            PurchaseItem(
                receipt_id=uuid.uuid4(),
                raw_name="Harpic",
                quantity=Decimal("6"),
                unit="bottle",
            )
        ]
        saved = await receipt_repo.save_purchase_items(mock_session, items)
        assert len(saved) == 1
        mock_session.add_all.assert_called_once_with(items)
        mock_session.flush.assert_awaited_once()


# ── Ingestion Service Tests ────────────────────────────────────────────────────

class TestIngestionService:

    def test_receipt_to_detail_response_formatter(self):
        dummy = _make_dummy_receipt()
        resp = receipt_to_detail_response(dummy)
        assert isinstance(resp, ReceiptDetailResponse)
        assert resp.id == dummy.id
        assert resp.supplier_name == "Sharma Traders"
        assert resp.item_count == 2
        assert resp.receipt_total == 2570.0
        assert resp.items[0].raw_name == "Rice"
        assert resp.items[0].unit_price == 74.0

    @pytest.mark.asyncio
    async def test_ingest_pipeline_rejects_non_receipt(self):
        mock_session = AsyncMock()
        jpeg = _make_jpeg_bytes()

        rejected_clf = ClassificationResult(
            is_receipt=False,
            confidence=0.15,
            document_type="random_photo",
            reason="not a receipt",
            latency_ms=50.0,
            accepted=False,
            threshold_used=0.75,
        )

        with patch("app.services.ingestion.classify_receipt_image", AsyncMock(return_value=rejected_clf)):
            with pytest.raises(NotAReceiptError):
                await ingest_receipt_pipeline(mock_session, image_bytes=jpeg, filename="photo.jpg")

    @pytest.mark.asyncio
    async def test_ingest_pipeline_rejects_corrupt_image(self):
        mock_session = AsyncMock()
        with pytest.raises(InvalidImageError):
            await ingest_receipt_pipeline(mock_session, image_bytes=b"corrupt bytes", filename="bad.jpg")

    @pytest.mark.asyncio
    async def test_ingest_pipeline_success(self):
        mock_session = AsyncMock()
        jpeg = _make_jpeg_bytes()

        accepted_clf = ClassificationResult(
            is_receipt=True,
            confidence=0.95,
            document_type="supplier_purchase_receipt",
            reason="valid receipt",
            latency_ms=100.0,
            accepted=True,
            threshold_used=0.75,
        )

        extracted = ExtractedReceipt(
            supplier="Sharma Traders",
            date="2026-09-29",
            invoice_number="ST-4821",
            receipt_total=1850.0,
            currency="INR",
            items=[
                ExtractedItem(
                    raw_name="Rice",
                    quantity=25.0,
                    unit="kg",
                    unit_price=74.0,
                    line_total=1850.0,
                )
            ],
            extraction_confidence=0.92,
        )

        supplier_mock = Supplier(
            id=uuid.uuid4(),
            name="Sharma Traders",
            normalized_name="sharma traders",
        )

        product_mock = MagicMock()
        product_mock.id = uuid.uuid4()
        product_mock.canonical_name = "Rice"

        res_result = ResolutionResult(
            raw_name="Rice",
            product=product_mock,
            confidence=0.95,
            method=ResolutionMethod.normalized,
        )

        with (
            patch("app.services.ingestion.classify_receipt_image", AsyncMock(return_value=accepted_clf)),
            patch("app.services.ingestion.extract_receipt", AsyncMock(return_value=extracted)),
            patch("app.repositories.product_repo.get_or_create_supplier", AsyncMock(return_value=supplier_mock)),
            patch("app.services.resolver.ProductResolver.resolve", AsyncMock(return_value=res_result)),
            patch("app.services.ingestion.embed_text", AsyncMock(return_value=[0.1] * 768)),
            patch("app.repositories.receipt_repo.create_receipt", AsyncMock(return_value=Receipt(
                id=uuid.uuid4(),
                supplier_id=supplier_mock.id,
                supplier_raw_name="Sharma Traders",
                receipt_date=date(2026, 9, 29),
                invoice_number="ST-4821",
                receipt_total=Decimal("1850.00"),
                currency="INR",
                status=ReceiptStatus.stored,
                extraction_confidence=0.92,
                classifier_confidence=0.95,
            ))),
            patch("app.repositories.receipt_repo.save_purchase_items", AsyncMock(return_value=[])),
        ):
            receipt = await ingest_receipt_pipeline(
                mock_session,
                image_bytes=jpeg,
                filename="receipt.jpg",
            )

            assert receipt is not None
            assert receipt.supplier_raw_name == "Sharma Traders"
            assert len(receipt.purchase_items) == 1
            assert receipt.purchase_items[0].raw_name == "Rice"
            assert receipt.purchase_items[0].product == product_mock


# ── API Endpoint Tests ─────────────────────────────────────────────────────────

class TestReceiptEndpoints:

    def test_upload_endpoint_success(self, client):
        dummy = _make_dummy_receipt()
        jpeg = _make_jpeg_bytes()

        with patch("app.api.receipts.ingest_receipt_pipeline", AsyncMock(return_value=dummy)):
            resp = client.post(
                "/api/v1/receipts/upload",
                files={"file": ("receipt.jpg", io.BytesIO(jpeg), "image/jpeg")},
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["supplier_name"] == "Sharma Traders"
        assert data["item_count"] == 2
        assert len(data["items"]) == 2
        assert data["items"][0]["raw_name"] == "Rice"
        assert data["receipt_total"] == 2570.0

    def test_upload_endpoint_rejects_non_receipt(self, client):
        jpeg = _make_jpeg_bytes()
        with patch(
            "app.api.receipts.ingest_receipt_pipeline",
            AsyncMock(side_effect=NotAReceiptError("Not a receipt", detail="low confidence")),
        ):
            resp = client.post(
                "/api/v1/receipts/upload",
                files={"file": ("screenshot.jpg", io.BytesIO(jpeg), "image/jpeg")},
            )

        assert resp.status_code == 422
        assert resp.json()["error"] == "NOT_A_RECEIPT"

    def test_upload_endpoint_corrupt_image(self, client):
        resp = client.post(
            "/api/v1/receipts/upload",
            files={"file": ("bad.jpg", io.BytesIO(b"notanimage"), "image/jpeg")},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_IMAGE"

    def test_get_receipt_success(self, client):
        dummy = _make_dummy_receipt()
        with patch("app.repositories.receipt_repo.get_receipt_by_id", AsyncMock(return_value=dummy)):
            resp = client.get(f"/api/v1/receipts/{dummy.id}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(dummy.id)
        assert data["supplier_name"] == "Sharma Traders"
        assert data["item_count"] == 2

    def test_get_receipt_not_found(self, client):
        random_id = uuid.uuid4()
        with patch("app.repositories.receipt_repo.get_receipt_by_id", AsyncMock(return_value=None)):
            resp = client.get(f"/api/v1/receipts/{random_id}")

        assert resp.status_code == 404
        assert resp.json()["error"] == "NOT_FOUND"

    def test_list_receipts_endpoint(self, client):
        dummy = _make_dummy_receipt()
        with patch(
            "app.repositories.receipt_repo.list_receipts",
            AsyncMock(return_value=([dummy], 1)),
        ):
            resp = client.get("/api/v1/receipts?limit=10&offset=0")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["receipts"]) == 1
        assert data["receipts"][0]["id"] == str(dummy.id)
