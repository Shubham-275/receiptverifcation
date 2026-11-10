"""
PurchaseItem model.

Represents a single line item on a receipt.

Design goals:
- Supports arbitrary products — no hardcoded categories.
- Stores both the raw extracted name (for traceability) and the
  resolved product_id (for analytics).
- Supports arbitrary units: kg, g, litre, ml, piece, packet, box,
  bottle, dozen, etc.
- Records how the product was resolved and with what confidence,
  enabling future auditing and re-resolution.
"""
import enum
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Enum, Float, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.product import Product
    from app.models.product_variant import ProductVariant
    from app.models.receipt import Receipt


class ResolutionMethod(str, enum.Enum):
    """How the raw product name was resolved to a canonical Product."""
    unresolved = "unresolved"       # not yet resolved
    exact_alias = "exact_alias"     # matched an existing ProductAlias exactly
    normalized = "normalized"       # matched after normalization
    fuzzy = "fuzzy"                 # matched via RapidFuzz
    vector = "vector"               # matched via pgvector similarity
    llm = "llm"                     # resolved by LLM contextual reasoning
    manual = "manual"               # confirmed by a human


class PurchaseItem(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "purchase_items"

    # ── Foreign keys ───────────────────────────────────────────────────────────
    receipt_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("receipts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="NULL if product could not be resolved",
    )
    product_variant_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Extracted data (preserved as-is for traceability) ─────────────────────
    raw_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Product name exactly as extracted from the receipt",
    )

    # ── Quantities and pricing ─────────────────────────────────────────────────
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(12, 3),
        nullable=False,
        comment="Number of units purchased",
    )
    unit: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Purchase unit: kg, g, litre, ml, piece, packet, box, bottle, dozen, etc.",
    )
    unit_price: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
        comment="Price per unit",
    )
    line_total: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
        comment="Total for this line: quantity × unit_price",
    )
    currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="INR",
    )

    # ── Resolution metadata ────────────────────────────────────────────────────
    resolution_method: Mapped[ResolutionMethod] = mapped_column(
        Enum(ResolutionMethod, name="resolution_method_enum"),
        nullable=False,
        default=ResolutionMethod.unresolved,
        comment="Which step of the resolution cascade resolved this item",
    )
    resolution_confidence: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Confidence score [0,1] in the product resolution",
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Extraction warnings, OCR ambiguities, validation notes",
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    receipt: Mapped["Receipt"] = relationship("Receipt", back_populates="purchase_items")
    product: Mapped["Product | None"] = relationship(
        "Product", back_populates="purchase_items"
    )
    product_variant: Mapped["ProductVariant | None"] = relationship(
        "ProductVariant", back_populates="purchase_items"
    )

    def __repr__(self) -> str:
        return (
            f"<PurchaseItem raw={self.raw_name!r} "
            f"qty={self.quantity} {self.unit} "
            f"total={self.line_total} "
            f"resolved={self.resolution_method}>"
        )
