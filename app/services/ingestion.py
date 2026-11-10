"""
Ingestion service — orchestrates the complete receipt processing pipeline:
1. Image validation (format, size, dimensions)
2. Receipt classification (reject non-receipts before expensive processing)
3. Multimodal extraction (supplier, date, total, arbitrary N line items)
4. Normalization and mathematical validation
5. Supplier identification / creation
6. Product entity resolution cascade (exact alias -> norm -> fuzzy -> vector -> LLM)
7. Semantic content embedding generation
8. Relational persistence to PostgreSQL (Receipt + PurchaseItems)
"""
from __future__ import annotations

import decimal
from decimal import Decimal
import uuid
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotAReceiptError
from app.logging_config import get_logger
from app.models.purchase_item import PurchaseItem, ResolutionMethod
from app.models.receipt import Receipt, ReceiptStatus
from app.repositories import product_repo, receipt_repo
from app.schemas.receipt import ReceiptDetailResponse, ReceiptItemDetail
from app.services.classifier import classify_receipt_image
from app.services.embedding import embed_text
from app.services.extractor import extract_receipt
from app.services.image_validator import validate_image
from app.services.normalizer import validate_and_normalise
from app.services.resolver import ProductResolver

log = get_logger(__name__)


def receipt_to_detail_response(receipt: Receipt) -> ReceiptDetailResponse:
    """Format an ORM Receipt instance into ReceiptDetailResponse."""
    items = []
    for item in receipt.purchase_items:
        prod_name = item.product.canonical_name if getattr(item, "product", None) else None
        res_method = (
            item.resolution_method.value
            if hasattr(item.resolution_method, "value")
            else str(item.resolution_method)
        )
        items.append(
            ReceiptItemDetail(
                id=item.id or uuid.uuid4(),
                receipt_id=item.receipt_id or receipt.id,
                raw_name=item.raw_name,
                product_id=item.product_id,
                product_name=prod_name,
                quantity=float(item.quantity) if item.quantity is not None else 0.0,
                unit=item.unit,
                unit_price=float(item.unit_price) if item.unit_price is not None else None,
                line_total=float(item.line_total) if item.line_total is not None else None,
                currency=item.currency or "INR",
                resolution_method=res_method,
                resolution_confidence=item.resolution_confidence,
                notes=item.notes,
            )
        )

    supplier_name = (
        receipt.supplier.name
        if getattr(receipt, "supplier", None)
        else receipt.supplier_raw_name
    )

    status_str = (
        receipt.status.value
        if hasattr(receipt.status, "value")
        else str(receipt.status)
    )

    return ReceiptDetailResponse(
        id=receipt.id,
        supplier_id=receipt.supplier_id,
        supplier_name=supplier_name,
        receipt_date=receipt.receipt_date,
        invoice_number=receipt.invoice_number,
        receipt_total=float(receipt.receipt_total) if receipt.receipt_total is not None else None,
        currency=receipt.currency or "INR",
        status=status_str,
        extraction_confidence=receipt.extraction_confidence,
        classifier_confidence=receipt.classifier_confidence,
        image_path=receipt.image_path,
        validation_notes=receipt.validation_notes,
        item_count=len(items),
        items=items,
        created_at=receipt.created_at,
        updated_at=receipt.updated_at,
    )


async def ingest_receipt_pipeline(
    session: AsyncSession,
    *,
    image_bytes: bytes,
    filename: str = "",
    image_path: str | None = None,
) -> Receipt:
    """
    Execute end-to-end receipt ingestion:
    Validate → Classify → Extract → Validate/Normalize → Resolve Products → Embed → Persist.

    Raises:
        InvalidImageError: If image is corrupt or unacceptable format.
        NotAReceiptError: If classifier determines image is not a receipt.
        AIServiceError: If Gemini API fails.
    """
    # 1. Validate image format and integrity
    img_info = validate_image(image_bytes, filename=filename)

    # 2. Gate with classifier
    clf = await classify_receipt_image(image_bytes, mime_type=img_info.mime_type)
    if not clf.accepted:
        log.warning(
            "receipt_ingestion_rejected",
            filename=filename,
            confidence=clf.confidence,
            reason=clf.reason,
        )
        raise NotAReceiptError(
            "Image was not accepted as a supplier receipt.",
            detail=f"confidence={clf.confidence:.2f} reason='{clf.reason}'",
        )

    # 3. Extract receipt data via Gemini Vision
    extracted = await extract_receipt(image_bytes, mime_type=img_info.mime_type)

    # 4. Normalize and validate mathematics & dates
    validation = validate_and_normalise(extracted)
    r = validation.receipt

    # 5. Supplier identification / creation
    supplier = None
    if r.supplier:
        supplier = await product_repo.get_or_create_supplier(session, r.supplier)

    supplier_id = supplier.id if supplier else None

    # 6. Product resolution cascade
    resolver = ProductResolver(session)
    resolved_items = []
    for item in r.items:
        res = await resolver.resolve(
            raw_name=item.raw_name,
            supplier_id=supplier_id,
            supplier_name=r.supplier,
            unit=item.unit,
        )
        resolved_items.append((item, res))

    # 7. Generate receipt content embedding for semantic retrieval
    content_embedding = None
    try:
        items_summary = ", ".join(
            f"{it.quantity} {it.unit} {it.raw_name} @ {it.unit_price or 'unknown'}"
            for it in r.items
        )
        embed_payload = (
            f"Supplier: {r.supplier or 'Unknown'}. "
            f"Date: {r.date or 'Unknown'}. "
            f"Total: {r.receipt_total or 'Unknown'}. "
            f"Items: {items_summary}"
        )
        content_embedding = await embed_text(embed_payload)
    except Exception as exc:
        log.warning("receipt_content_embedding_failed", error=str(exc))

    # 8. Relational persistence
    receipt_total_dec = (
        Decimal(str(r.receipt_total)) if r.receipt_total is not None else None
    )

    receipt = await receipt_repo.create_receipt(
        session,
        supplier_id=supplier_id,
        supplier_raw_name=r.supplier,
        receipt_date=validation.receipt_date,
        invoice_number=r.invoice_number,
        receipt_total=receipt_total_dec,
        currency=r.currency,
        status=ReceiptStatus.stored,
        extraction_confidence=r.extraction_confidence,
        classifier_confidence=clf.confidence,
        image_path=image_path or filename,
        raw_extraction=extracted.model_dump(mode="json"),
        validation_notes=validation.summary,
        content_embedding=content_embedding,
    )

    # Create PurchaseItem records
    db_items: list[PurchaseItem] = []
    for item, res in resolved_items:
        qty_dec = Decimal(str(item.quantity)) if item.quantity is not None else Decimal("1")
        unit_price_dec = (
            Decimal(str(item.unit_price)) if item.unit_price is not None else None
        )
        line_total_dec = (
            Decimal(str(item.line_total)) if item.line_total is not None else None
        )

        p_item = PurchaseItem(
            id=uuid.uuid4(),
            receipt_id=receipt.id,
            product_id=res.product.id if res.product else None,
            raw_name=item.raw_name,
            quantity=qty_dec,
            unit=item.unit,
            unit_price=unit_price_dec,
            line_total=line_total_dec,
            currency=r.currency,
            resolution_method=res.method,
            resolution_confidence=res.confidence,
            notes=res.notes,
        )
        # Link product relation in memory for immediate response serialization
        if res.product:
            p_item.product = res.product

        db_items.append(p_item)

    await receipt_repo.save_purchase_items(session, db_items)

    # Reload with relationships eagerly loaded so response serialization
    # never triggers a lazy load (which fails in async context).
    receipt_id = receipt.id
    try:
        reloaded = await receipt_repo.get_receipt_by_id(session, receipt_id)
    except Exception:  # pragma: no cover - fall back to in-memory graph
        reloaded = None
    if isinstance(reloaded, Receipt):
        receipt = reloaded
    else:
        receipt.supplier = supplier
        receipt.purchase_items = db_items

    log.info(
        "receipt_ingested_successfully",
        receipt_id=str(receipt.id),
        supplier=r.supplier,
        item_count=len(db_items),
        total=float(receipt_total_dec) if receipt_total_dec else None,
    )

    return receipt
