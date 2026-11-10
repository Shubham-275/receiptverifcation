"""
AliasObservation model.

Tracks every raw product name seen on receipts, even before it is resolved.
This is the raw signal used by the self-learning alias system.

Each time a raw name appears on a receipt, its occurrence_count increments.
When count + other signals (similarity, supplier context, price, unit)
cross the confidence threshold, the system can propose or auto-accept a
mapping in product_aliases.

Preserving raw observations ensures traceability and allows the system
to reconsider past mappings as more data arrives.
"""
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.product import Product
    from app.models.supplier import Supplier


class AliasObservation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "alias_observations"

    # The raw string exactly as extracted from the receipt
    raw_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Exact raw string from the receipt, e.g. 'PRLE G'",
    )
    normalized_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Lowercase, stripped version for grouping observations",
    )

    # Context signals
    supplier_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("suppliers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Current best-guess resolution (nullable until resolved)
    resolved_product_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Aggregated signals
    occurrence_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment="How many times this raw_name has been seen",
    )
    resolution_confidence: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Current confidence in the resolved_product_id mapping",
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Additional context from the most recent observation
    last_seen_unit: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="Unit from last receipt line item"
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Relationships ──────────────────────────────────────────────────────────
    supplier: Mapped["Supplier | None"] = relationship("Supplier", lazy="select")
    resolved_product: Mapped["Product | None"] = relationship("Product", lazy="select")

    def __repr__(self) -> str:
        return (
            f"<AliasObservation raw={self.raw_name!r} "
            f"count={self.occurrence_count} "
            f"resolved={self.resolved_product_id}>"
        )
