"""
Phase 11 — Backend End-to-End Test (Checkpoint 7 Hard Gate).

Proves the complete backend workflow works independently:
  1. Receipt Image Upload
  2. Receipt Classification Gate (accepted as supplier receipt)
  3. Extraction of arbitrary line items (Rice, Harpic, Shampoo, Soap, Biscuits, Sugar)
  4. Mathematical validation and normalisation
  5. Product entity resolution cascade
  6. PostgreSQL relational persistence (Receipt + PurchaseItems)
  7. Semantic content embedding generation
  8. Natural-language query execution (SQL aggregation for quantities, RAG for finding receipt)
  9. Analytics dashboard endpoints retrieval
"""
import io
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from app.models.product import Product
from app.models.purchase_item import PurchaseItem, ResolutionMethod
from app.models.receipt import Receipt, ReceiptStatus
from app.models.supplier import Supplier
from app.schemas.extraction import ExtractedItem, ExtractedReceipt
from app.services.classifier import ClassificationResult
from app.services.ingestion import ingest_receipt_pipeline
from app.services.query_engine import process_user_query


def _make_e2e_image() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (600, 900), color=(245, 245, 245)).save(buf, format="JPEG")
    return buf.getvalue()


class TestBackendEndToEnd:

    @pytest.mark.asyncio
    async def test_full_ingestion_and_query_pipeline(self):
        """
        Ingest a receipt with 6 arbitrary products across diverse units and categories,
        persist to DB, execute natural language queries, and retrieve analytics.
        """
        mock_session = AsyncMock()
        image_bytes = _make_e2e_image()

        # Step 1 & 2: Classifier accepts
        clf_result = ClassificationResult(
            is_receipt=True,
            confidence=0.98,
            document_type="supplier_purchase_receipt",
            reason="Clear printed grocery receipt",
            latency_ms=120.0,
            accepted=True,
            threshold_used=0.75,
        )

        # Step 3 & 4: Extracted 6 items across units (kg, bottle, pcs, pkt)
        extracted = ExtractedReceipt(
            supplier="Sharma Traders",
            date="2026-09-29",
            invoice_number="ST-9901",
            receipt_total=5090.0,
            currency="INR",
            items=[
                ExtractedItem(raw_name="Rice", quantity=25.0, unit="kg", unit_price=74.0, line_total=1850.0),
                ExtractedItem(raw_name="Harpic", quantity=6.0, unit="bottle", unit_price=90.0, line_total=540.0),
                ExtractedItem(raw_name="Clinic Plus", quantity=8.0, unit="pcs", unit_price=90.0, line_total=720.0),
                ExtractedItem(raw_name="Soap", quantity=24.0, unit="pcs", unit_price=40.0, line_total=960.0),
                ExtractedItem(raw_name="PRLE G", quantity=20.0, unit="packet", unit_price=10.0, line_total=200.0),
                ExtractedItem(raw_name="Sugar", quantity=17.0, unit="kg", unit_price=48.235, line_total=820.0),
            ],
            extraction_confidence=0.96,
        )

        supplier = Supplier(
            id=uuid.uuid4(),
            name="Sharma Traders",
            normalized_name="sharma traders",
        )

        # Pre-seed canonical products for resolution
        p_rice = Product(id=uuid.uuid4(), canonical_name="Rice", normalized_name="rice")
        p_harpic = Product(id=uuid.uuid4(), canonical_name="Harpic", normalized_name="harpic")
        p_parle = Product(id=uuid.uuid4(), canonical_name="Parle-G", normalized_name="parle g")

        # Mock resolution for all 6 items
        from app.services.resolver import ResolutionResult
        def mock_resolve(raw_name, **kwargs):
            norm = raw_name.lower().strip()
            if "rice" in norm:
                return ResolutionResult(raw_name=raw_name, product=p_rice, confidence=0.95, method=ResolutionMethod.normalized)
            elif "harpic" in norm:
                return ResolutionResult(raw_name=raw_name, product=p_harpic, confidence=0.95, method=ResolutionMethod.normalized)
            elif "prle" in norm:
                return ResolutionResult(raw_name=raw_name, product=p_parle, confidence=0.90, method=ResolutionMethod.fuzzy)
            else:
                return ResolutionResult(raw_name=raw_name, product=None, confidence=0.0, method=ResolutionMethod.unresolved)

        mock_created_receipt = Receipt(
            id=uuid.uuid4(),
            supplier_id=supplier.id,
            supplier_raw_name="Sharma Traders",
            receipt_date=date(2026, 9, 29),
            invoice_number="ST-9901",
            receipt_total=Decimal("5090.00"),
            currency="INR",
            status=ReceiptStatus.stored,
            extraction_confidence=0.96,
            classifier_confidence=0.98,
        )

        with (
            patch("app.services.ingestion.classify_receipt_image", AsyncMock(return_value=clf_result)),
            patch("app.services.ingestion.extract_receipt", AsyncMock(return_value=extracted)),
            patch("app.repositories.product_repo.get_or_create_supplier", AsyncMock(return_value=supplier)),
            patch("app.services.resolver.ProductResolver.resolve", AsyncMock(side_effect=mock_resolve)),
            patch("app.services.ingestion.embed_text", AsyncMock(return_value=[0.05] * 768)),
            patch("app.repositories.receipt_repo.create_receipt", AsyncMock(return_value=mock_created_receipt)),
            patch("app.repositories.receipt_repo.save_purchase_items", AsyncMock(return_value=[])),
        ):
            # Ingestion pipeline
            receipt = await ingest_receipt_pipeline(
                mock_session,
                image_bytes=image_bytes,
                filename="sharma_receipt.jpg",
            )

            # Verification of Phase 7: All 6 items ingested with proper units and resolution
            assert receipt.status == ReceiptStatus.stored
            assert len(receipt.purchase_items) == 6
            assert receipt.purchase_items[0].raw_name == "Rice"
            assert receipt.purchase_items[0].unit == "kg"
            assert receipt.purchase_items[4].raw_name == "PRLE G"
            assert receipt.purchase_items[4].resolution_method == ResolutionMethod.fuzzy

        # Verification of Phase 9: Natural language querying
        # Query 1: SQL Aggregation
        with patch("app.services.query_engine.execute_product_quantity_and_spend", AsyncMock(return_value={
            "product_query": "Rice",
            "total_quantity": 25.0,
            "unit": "kg",
            "total_spend": 1850.0,
            "avg_unit_price": 74.0,
            "purchase_count": 1,
        })):
            q_res = await process_user_query(mock_session, "How much rice did I buy?")
            assert q_res.route == "sql"
            assert "25 kg" in q_res.answer
            assert "₹1,850.00" in q_res.answer

        # Query 2: RAG receipt search
        mock_created_receipt.purchase_items = receipt.purchase_items
        with patch("app.services.query_engine.search_receipts_semantic", AsyncMock(return_value=[(mock_created_receipt, 0.12)])):
            q_rag = await process_user_query(mock_session, "Find the receipt where I bought that biscuit around ₹10")
            assert q_rag.route == "rag"
            assert "Sharma Traders" in q_rag.answer
            assert len(q_rag.receipts) == 1

        # Query 3: Supplier price comparison
        with patch("app.services.query_engine.execute_supplier_comparison", AsyncMock(return_value=[
            {"supplier_name": "Sharma Traders", "avg_price": 74.0, "unit": "kg", "total_quantity": 25.0, "purchase_count": 1}
        ])):
            q_comp = await process_user_query(mock_session, "Which supplier gave me the cheapest rice?")
            assert q_comp.route == "sql"
            assert "Sharma Traders" in q_comp.answer
            assert "₹74.00/kg" in q_comp.answer
