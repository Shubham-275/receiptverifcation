"""
Tests for Batch Upload & Batch Extraction endpoints:
- POST /api/v1/receipts/batch-extract
- POST /api/v1/receipts/batch-upload
"""
import io
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image
from sqlalchemy.exc import OperationalError

from app.exceptions import InvalidImageError, NotAReceiptError
from app.models.purchase_item import PurchaseItem, ResolutionMethod
from app.models.receipt import Receipt, ReceiptStatus
from app.models.supplier import Supplier
from app.schemas.receipt import ExtractResponse, ExtractedItemResponse


def _make_jpeg_bytes(width: int = 400, height: int = 600) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(240, 240, 240)).save(buf, format="JPEG")
    return buf.getvalue()


def _make_dummy_receipt(supplier_name="Sharma Traders", total=2570.0):
    r_id = uuid.uuid4()
    s_id = uuid.uuid4()
    supplier = Supplier(id=s_id, name=supplier_name, normalized_name=supplier_name.lower())
    receipt = Receipt(
        id=r_id,
        supplier_id=s_id,
        supplier_raw_name=supplier_name,
        receipt_date=date(2026, 9, 29),
        invoice_number="INV-1001",
        receipt_total=Decimal(str(total)),
        currency="INR",
        status=ReceiptStatus.stored,
        extraction_confidence=0.95,
        classifier_confidence=0.98,
        image_path="test_receipt.jpg",
        validation_notes="Totals matched",
        supplier=supplier,
        purchase_items=[
            PurchaseItem(
                id=uuid.uuid4(),
                receipt_id=r_id,
                raw_name="Rice",
                quantity=Decimal("25"),
                unit="kg",
                unit_price=Decimal("60.00"),
                line_total=Decimal("1500.00"),
                currency="INR",
                resolution_method=ResolutionMethod.fuzzy,
                resolution_confidence=0.90,
            ),
            PurchaseItem(
                id=uuid.uuid4(),
                receipt_id=r_id,
                raw_name="Sugar",
                quantity=Decimal("25"),
                unit="kg",
                unit_price=Decimal("42.80"),
                line_total=Decimal("1070.00"),
                currency="INR",
                resolution_method=ResolutionMethod.fuzzy,
                resolution_confidence=0.92,
            ),
        ],
    )
    return receipt


class TestBatchEndpoints:
    def test_batch_extract_success(self, client):
        dummy_extract = ExtractResponse(
            classifier_confidence=0.98,
            supplier="Fresh Mart",
            date="2026-10-31",
            invoice_number="INV-88",
            receipt_total=1250.50,
            currency="INR",
            items=[
                ExtractedItemResponse(raw_name="Milk", quantity=2.0, unit="pkt", unit_price=30.0, line_total=60.0),
                ExtractedItemResponse(raw_name="Bread", quantity=1.0, unit="pcs", unit_price=45.0, line_total=45.0),
            ],
            item_count=2,
            extraction_confidence=0.96,
            extraction_notes=None,
            validation_summary="Items matched",
            total_match=True,
            item_math_failures=0,
            is_valid=True,
        )

        jpeg1 = _make_jpeg_bytes()
        jpeg2 = _make_jpeg_bytes()

        with patch("app.api.receipts._process_single_extract", AsyncMock(return_value=dummy_extract)):
            resp = client.post(
                "/api/v1/receipts/batch-extract",
                files=[
                    ("files", ("receipt1.jpg", io.BytesIO(jpeg1), "image/jpeg")),
                    ("files", ("receipt2.jpg", io.BytesIO(jpeg2), "image/jpeg")),
                ],
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_files"] == 2
        assert data["successful_count"] == 2
        assert data["failed_count"] == 0
        assert data["total_spend"] == 2501.0
        assert data["total_items"] == 4
        assert len(data["results"]) == 2
        assert data["results"][0]["filename"] == "receipt1.jpg"
        assert data["results"][0]["success"] is True
        assert data["results"][0]["extraction"]["supplier"] == "Fresh Mart"

    def test_batch_extract_mixed_results(self, client):
        good_extract = ExtractResponse(
            classifier_confidence=0.99,
            supplier="Gupta Grocers",
            date="2026-10-30",
            invoice_number="INV-12",
            receipt_total=500.0,
            currency="INR",
            items=[
                ExtractedItemResponse(raw_name="Tea", quantity=1.0, unit="box", unit_price=500.0, line_total=500.0),
            ],
            item_count=1,
            extraction_confidence=0.95,
            extraction_notes=None,
            validation_summary="Totals matched",
            total_match=True,
            item_math_failures=0,
            is_valid=True,
        )

        async def _mock_extract(data, filename):
            if filename == "good.jpg":
                return good_extract
            elif filename == "bad_receipt.jpg":
                raise NotAReceiptError("Image not recognized as receipt", detail="Confidence 0.12")
            else:
                raise InvalidImageError("File is corrupted")

        jpeg = _make_jpeg_bytes()

        with patch("app.api.receipts._process_single_extract", side_effect=_mock_extract):
            resp = client.post(
                "/api/v1/receipts/batch-extract",
                files=[
                    ("files", ("good.jpg", io.BytesIO(jpeg), "image/jpeg")),
                    ("files", ("bad_receipt.jpg", io.BytesIO(jpeg), "image/jpeg")),
                    ("files", ("corrupted.jpg", io.BytesIO(b"garbage"), "image/jpeg")),
                ],
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_files"] == 3
        assert data["successful_count"] == 1
        assert data["failed_count"] == 2
        assert data["total_spend"] == 500.0
        assert data["total_items"] == 1

        assert data["results"][0]["success"] is True
        assert data["results"][0]["filename"] == "good.jpg"

        assert data["results"][1]["success"] is False
        assert data["results"][1]["status_code"] == 422
        assert "not recognized" in data["results"][1]["error"]

        assert data["results"][2]["success"] is False
        assert data["results"][2]["status_code"] == 400
        assert "corrupted" in data["results"][2]["error"].lower()

    def test_batch_upload_success(self, client):
        dummy1 = _make_dummy_receipt(supplier_name="Supplier Alpha", total=1000.0)
        dummy2 = _make_dummy_receipt(supplier_name="Supplier Beta", total=2000.0)

        mock_ingest = AsyncMock(side_effect=[dummy1, dummy2])
        jpeg = _make_jpeg_bytes()

        with patch("app.api.receipts.ingest_receipt_pipeline", mock_ingest):
            resp = client.post(
                "/api/v1/receipts/batch-upload",
                files=[
                    ("files", ("receipt_a.jpg", io.BytesIO(jpeg), "image/jpeg")),
                    ("files", ("receipt_b.jpg", io.BytesIO(jpeg), "image/jpeg")),
                ],
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_files"] == 2
        assert data["successful_count"] == 2
        assert data["failed_count"] == 0
        assert data["total_spend"] == 3000.0
        assert data["total_items"] == 4
        assert data["results"][0]["filename"] == "receipt_a.jpg"
        assert data["results"][0]["receipt"]["supplier_name"] == "Supplier Alpha"
        assert data["results"][1]["filename"] == "receipt_b.jpg"
        assert data["results"][1]["receipt"]["supplier_name"] == "Supplier Beta"

    def test_batch_upload_partial_failure(self, client):
        dummy = _make_dummy_receipt(supplier_name="Alpha Store", total=750.0)

        async def _mock_ingest(session, image_bytes, filename, image_path):
            if filename == "valid.jpg":
                return dummy
            raise NotAReceiptError("Selfie detected, not a receipt")

        jpeg = _make_jpeg_bytes()

        with patch("app.api.receipts.ingest_receipt_pipeline", side_effect=_mock_ingest):
            resp = client.post(
                "/api/v1/receipts/batch-upload",
                files=[
                    ("files", ("valid.jpg", io.BytesIO(jpeg), "image/jpeg")),
                    ("files", ("selfie.jpg", io.BytesIO(jpeg), "image/jpeg")),
                ],
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_files"] == 2
        assert data["successful_count"] == 1
        assert data["failed_count"] == 1
        assert data["results"][0]["success"] is True
        assert data["results"][0]["receipt"]["supplier_name"] == "Alpha Store"
        assert data["results"][1]["success"] is False
        assert data["results"][1]["status_code"] == 422
        assert "Selfie" in data["results"][1]["error"]

    def test_batch_upload_db_offline_raises_503(self, client):
        jpeg = _make_jpeg_bytes()

        with patch(
            "app.api.receipts.ingest_receipt_pipeline",
            AsyncMock(side_effect=OperationalError("Connection refused", None, None)),
        ):
            resp = client.post(
                "/api/v1/receipts/batch-upload",
                files=[
                    ("files", ("receipt.jpg", io.BytesIO(jpeg), "image/jpeg")),
                ],
            )

        assert resp.status_code == 503
        assert "PostgreSQL database is currently offline" in resp.json()["detail"]
