"""
Product model.

Represents a canonical product identity — the "ground truth" name
that all aliases and raw receipt names resolve to.

Examples of canonical names:
  Parle-G, Harpic, Clinic Plus, Rice, Sugar

The name_embedding column stores a pgvector vector for semantic similarity
search when exact/fuzzy matching fails during product resolution.
"""
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.product_alias import ProductAlias
    from app.models.product_variant import ProductVariant
    from app.models.purchase_item import PurchaseItem

# Dimension of the Gemini embedding model output.
EMBEDDING_DIM = 768


class Product(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "products"

    canonical_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
        comment="The authoritative product name, e.g. 'Parle-G'",
    )
    normalized_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Lowercase, stripped name for fast exact/fuzzy matching",
    )
    category: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        comment="Optional broad category, e.g. 'biscuit', 'cleaning'. NOT hardcoded.",
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # pgvector column for semantic similarity search.
    # NULL until embeddings are generated for this product.
    name_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM),
        nullable=True,
        comment="Embedding of canonical_name for pgvector similarity search",
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    aliases: Mapped[list["ProductAlias"]] = relationship(
        "ProductAlias", back_populates="product", lazy="select", cascade="all, delete-orphan"
    )
    variants: Mapped[list["ProductVariant"]] = relationship(
        "ProductVariant", back_populates="product", lazy="select", cascade="all, delete-orphan"
    )
    purchase_items: Mapped[list["PurchaseItem"]] = relationship(
        "PurchaseItem", back_populates="product", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<Product id={self.id} canonical_name={self.canonical_name!r}>"
