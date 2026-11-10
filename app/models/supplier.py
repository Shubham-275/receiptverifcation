"""
Supplier model.

Represents a wholesaler/supplier who issues receipts.
A supplier's name matters for alias resolution context
(the same abbreviation can mean different products at different suppliers).
"""
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.product_alias import ProductAlias
    from app.models.receipt import Receipt


class Supplier(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "suppliers"

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    normalized_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Lowercase, stripped version of name for fast matching",
    )
    contact_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Relationships ──────────────────────────────────────────────────────────
    receipts: Mapped[list["Receipt"]] = relationship(
        "Receipt", back_populates="supplier", lazy="select"
    )
    product_aliases: Mapped[list["ProductAlias"]] = relationship(
        "ProductAlias", back_populates="supplier", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<Supplier id={self.id} name={self.name!r}>"
