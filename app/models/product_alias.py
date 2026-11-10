"""
ProductAlias model.

Maps a known alias (raw name variant) to a canonical Product.
Aliases can be supplier-specific — the same abbreviation may mean
different products at different suppliers (e.g. "PG" → Parle-G at
Supplier A, but "PG" → Priya Gold at Supplier C).

Resolution pipeline uses this table first (cheap DB lookup) before
falling back to fuzzy/vector matching.
"""
import enum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.product import Product
    from app.models.supplier import Supplier


class AliasSource(str, enum.Enum):
    """How this alias was created."""
    manual = "manual"           # explicitly added by a human
    auto_learned = "auto_learned"   # inferred by the system with high confidence
    extracted = "extracted"     # first seen during receipt extraction (unconfirmed)


class ProductAlias(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "product_aliases"
    __table_args__ = (
        # An alias is unique within a supplier context.
        # NULL supplier_id means the alias is global (supplier-agnostic).
        UniqueConstraint(
            "normalized_alias", "supplier_id",
            name="uq_alias_normalized_supplier",
        ),
    )

    product_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    supplier_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("suppliers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="NULL = global alias; non-NULL = supplier-specific alias",
    )

    alias: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Raw alias string as seen on receipts, e.g. 'PRLE G'",
    )
    normalized_alias: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Lowercase, stripped alias for fast exact lookup",
    )

    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
        comment="Confidence score [0,1] for this alias mapping",
    )
    is_confirmed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="True if confirmed by a human or by auto-accept threshold",
    )
    source: Mapped[AliasSource] = mapped_column(
        Enum(AliasSource, name="alias_source_enum"),
        nullable=False,
        default=AliasSource.extracted,
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    product: Mapped["Product"] = relationship("Product", back_populates="aliases")
    supplier: Mapped["Supplier | None"] = relationship(
        "Supplier", back_populates="product_aliases"
    )

    def __repr__(self) -> str:
        return (
            f"<ProductAlias alias={self.alias!r} "
            f"→ product_id={self.product_id} "
            f"confidence={self.confidence:.2f} confirmed={self.is_confirmed}>"
        )
