"""
Receipt model.

Represents a single supplier receipt/invoice that has been uploaded
and processed by the system.

Key design decisions:
- supplier_raw_name: preserves what was on the image even if we can't
  match it to a known supplier immediately.
- raw_extraction: stores the full Gemini JSON output for traceability
  and re-processing if the schema changes.
- content_embedding: pgvector embedding of the receipt's text content,
  used for semantic receipt search ("find the receipt where I bought
  that biscuit for ₹10").
"""
import enum
from decimal import Decimal
from datetime import date
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import Date, Enum, Float, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin
from app.models.product import EMBEDDING_DIM

if TYPE_CHECKING:
    from app.models.supplier import Supplier
    from app.models.purchase_item import PurchaseItem


class ReceiptStatus(str, enum.Enum):
    pending = "pending"         # uploaded, not yet processed
    processing = "processing"   # currently being extracted
    validated = "validated"     # extracted + validated, ready to store
    stored = "stored"           # purchase items written to DB
    failed = "failed"           # extraction or validation failed
    rejected = "rejected"       # classified as NOT a receipt


class Receipt(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "receipts"

    # ── Supplier ───────────────────────────────────────────────────────────────
    supplier_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("suppliers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    supplier_raw_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Supplier name exactly as extracted from the receipt image",
    )

    # ── Receipt metadata ───────────────────────────────────────────────────────
    receipt_date: Mapped[date | None] = mapped_column(
        Date, nullable=True, index=True, comment="Date printed on the receipt"
    )
    invoice_number: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="Invoice/bill number if present"
    )
    receipt_total: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
        comment="Total amount as printed on the receipt",
    )
    currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="INR",
    )

    # ── Processing state ───────────────────────────────────────────────────────
    status: Mapped[ReceiptStatus] = mapped_column(
        Enum(ReceiptStatus, name="receipt_status_enum"),
        nullable=False,
        default=ReceiptStatus.pending,
        index=True,
    )
    extraction_confidence: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Overall confidence score from the Gemini extraction",
    )
    classifier_confidence: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Confidence that this image is a supplier receipt",
    )

    # ── Raw data preservation ──────────────────────────────────────────────────
    image_path: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="Path or URL to the stored receipt image",
    )
    raw_extraction: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Full JSON output from Gemini Vision — preserved for traceability",
    )
    validation_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Notes from the validation step (mismatches, warnings, etc.)",
    )

    # pgvector embedding for semantic receipt search
    content_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM),
        nullable=True,
        comment="Embedding of receipt text content for semantic search",
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    supplier: Mapped["Supplier | None"] = relationship(
        "Supplier", back_populates="receipts"
    )
    purchase_items: Mapped[list["PurchaseItem"]] = relationship(
        "PurchaseItem",
        back_populates="receipt",
        lazy="select",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<Receipt id={self.id} "
            f"supplier={self.supplier_raw_name!r} "
            f"date={self.receipt_date} "
            f"status={self.status}>"
        )
