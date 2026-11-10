"""
Receipt repository — DB operations for receipts and purchase items.

Keeps SQL queries separated from endpoints and ingestion services.
All functions accept an AsyncSession and return ORM objects or data tuples.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Sequence

from pgvector.sqlalchemy import Vector
from sqlalchemy import cast, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.purchase_item import PurchaseItem
from app.models.receipt import Receipt, ReceiptStatus
from app.models.supplier import Supplier


async def create_receipt(
    session: AsyncSession,
    *,
    supplier_id: uuid.UUID | None,
    supplier_raw_name: str | None,
    receipt_date: date | None,
    invoice_number: str | None,
    receipt_total: float | None,
    currency: str = "INR",
    status: ReceiptStatus = ReceiptStatus.stored,
    extraction_confidence: float | None = None,
    classifier_confidence: float | None = None,
    image_path: str | None = None,
    raw_extraction: dict | None = None,
    validation_notes: str | None = None,
    content_embedding: list[float] | None = None,
) -> Receipt:
    """Create and persist a new Receipt record."""
    receipt = Receipt(
        supplier_id=supplier_id,
        supplier_raw_name=supplier_raw_name,
        receipt_date=receipt_date,
        invoice_number=invoice_number,
        receipt_total=receipt_total,
        currency=currency,
        status=status,
        extraction_confidence=extraction_confidence,
        classifier_confidence=classifier_confidence,
        image_path=image_path,
        raw_extraction=raw_extraction,
        validation_notes=validation_notes,
        content_embedding=content_embedding,
    )
    session.add(receipt)
    await session.flush()
    return receipt


async def get_receipt_by_id(
    session: AsyncSession, receipt_id: uuid.UUID
) -> Receipt | None:
    """Fetch a single Receipt with its items, products, and supplier loaded."""
    stmt = (
        select(Receipt)
        .options(
            selectinload(Receipt.purchase_items).selectinload(PurchaseItem.product),
            selectinload(Receipt.supplier),
        )
        .where(Receipt.id == receipt_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_receipts(
    session: AsyncSession,
    *,
    supplier_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    status: ReceiptStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Receipt], int]:
    """
    List receipts matching optional filters with pagination.
    Returns (receipts_list, total_count).
    """
    conditions = []
    if supplier_id is not None:
        conditions.append(Receipt.supplier_id == supplier_id)
    if date_from is not None:
        conditions.append(Receipt.receipt_date >= date_from)
    if date_to is not None:
        conditions.append(Receipt.receipt_date <= date_to)
    if status is not None:
        conditions.append(Receipt.status == status)

    # Count query
    count_stmt = select(func.count(Receipt.id))
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    count_result = await session.execute(count_stmt)
    total = count_result.scalar_one() or 0

    # Data query
    stmt = (
        select(Receipt)
        .options(
            selectinload(Receipt.purchase_items).selectinload(PurchaseItem.product),
            selectinload(Receipt.supplier),
        )
        .order_by(Receipt.receipt_date.desc().nullslast(), Receipt.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if conditions:
        stmt = stmt.where(*conditions)

    result = await session.execute(stmt)
    receipts = list(result.scalars().all())
    return receipts, total


async def save_purchase_items(
    session: AsyncSession, items: Sequence[PurchaseItem]
) -> list[PurchaseItem]:
    """Persist a list of PurchaseItem records in bulk."""
    session.add_all(items)
    await session.flush()
    return list(items)


async def search_receipts_by_vector(
    session: AsyncSession,
    embedding: list[float],
    limit: int = 5,
) -> list[tuple[Receipt, float]]:
    """
    Find receipts whose content_embedding is closest to the query embedding.
    Returns list of (Receipt, cosine_distance) sorted ascending by distance.
    """
    embedding_literal = cast(literal(str(embedding)), Vector(len(embedding)))

    stmt = (
        select(
            Receipt,
            Receipt.content_embedding.cosine_distance(embedding_literal).label("distance"),
        )
        .options(
            selectinload(Receipt.purchase_items).selectinload(PurchaseItem.product),
            selectinload(Receipt.supplier),
        )
        .where(Receipt.content_embedding.is_not(None))
        .order_by("distance")
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [(row.Receipt, float(row.distance)) for row in result]
