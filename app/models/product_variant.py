"""
ProductVariant model.

Captures pack-size / variant information for a product.

Example:
  Product:    Shampoo
  Variant:    180ml bottle  (pack_size=180, pack_unit="ml", purchase_unit="bottle")

This allows the same product to appear in multiple pack sizes on different
receipts without collapsing them into an ambiguous single entry.
"""
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.product import Product
    from app.models.purchase_item import PurchaseItem


class ProductVariant(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "product_variants"

    product_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Pack size descriptor (optional — not every product has a defined pack size)
    pack_size: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 3),
        nullable=True,
        comment="Numeric pack size, e.g. 180 for 180ml shampoo",
    )
    pack_unit: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Unit of the pack size, e.g. 'ml', 'g', 'litre'",
    )
    purchase_unit: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment="Unit in which the product is purchased, e.g. 'bottle', 'packet', 'box'",
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Human-readable description, e.g. '180ml bottle'",
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    product: Mapped["Product"] = relationship("Product", back_populates="variants")
    purchase_items: Mapped[list["PurchaseItem"]] = relationship(
        "PurchaseItem", back_populates="product_variant", lazy="select"
    )

    def __repr__(self) -> str:
        return (
            f"<ProductVariant id={self.id} "
            f"pack={self.pack_size}{self.pack_unit} "
            f"unit={self.purchase_unit!r}>"
        )
