"""
Product repository — all DB operations for product resolution.

Keeps SQL out of the resolver service so the resolver is fully testable
by injecting a mock session.

All functions accept an AsyncSession and return ORM objects or None.
No business logic lives here — only data access.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alias_observation import AliasObservation
from app.models.product import Product
from app.models.product_alias import AliasSource, ProductAlias
from app.models.supplier import Supplier


# ── Text normalisation ─────────────────────────────────────────────────────────

def normalise_product_name(raw: str) -> str:
    """
    Produce a normalized key for fast exact matching.
    Lowercases, strips leading/trailing whitespace, collapses inner whitespace,
    and removes common punctuation (hyphens, dots, slashes).

    Examples:
        "PRLE G"   → "prle g"
        "Parle-G"  → "parle g"
        "PARLEG"   → "parleg"
        "P.G."     → "p g"
    """
    import re
    s = raw.strip().lower()
    s = re.sub(r"[-./\\|,]", " ", s)   # punctuation → space
    s = re.sub(r"\s+", " ", s)          # collapse spaces
    return s.strip()


# ── Supplier lookup ────────────────────────────────────────────────────────────

async def find_supplier_by_name(
    session: AsyncSession, raw_name: str
) -> Supplier | None:
    """Find a Supplier by normalised name."""
    norm = normalise_product_name(raw_name)
    result = await session.execute(
        select(Supplier).where(Supplier.normalized_name == norm)
    )
    return result.scalar_one_or_none()


async def get_or_create_supplier(
    session: AsyncSession, raw_name: str
) -> Supplier:
    """Return existing Supplier or create a new one."""
    existing = await find_supplier_by_name(session, raw_name)
    if existing:
        return existing
    supplier = Supplier(
        name=raw_name.strip(),
        normalized_name=normalise_product_name(raw_name),
    )
    session.add(supplier)
    await session.flush()   # get the ID without committing
    return supplier


# ── Alias lookup ───────────────────────────────────────────────────────────────

async def find_alias_exact(
    session: AsyncSession,
    normalised_alias: str,
    supplier_id: uuid.UUID | None = None,
) -> ProductAlias | None:
    """
    Exact alias lookup — supplier-scoped first, then global.

    Lookup order:
      1. (normalised_alias, supplier_id)  — supplier-specific
      2. (normalised_alias, NULL)         — global alias

    Returns the most specific match.
    """
    # Supplier-scoped
    if supplier_id is not None:
        result = await session.execute(
            select(ProductAlias)
            .where(
                ProductAlias.normalized_alias == normalised_alias,
                ProductAlias.supplier_id == supplier_id,
            )
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row:
            return row

    # Global
    result = await session.execute(
        select(ProductAlias)
        .where(
            ProductAlias.normalized_alias == normalised_alias,
            ProductAlias.supplier_id.is_(None),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


# ── Product direct lookup ──────────────────────────────────────────────────────

async def find_product_by_normalised_name(
    session: AsyncSession, normalised_name: str
) -> Product | None:
    """Direct lookup against products.normalized_name."""
    result = await session.execute(
        select(Product).where(Product.normalized_name == normalised_name).limit(1)
    )
    return result.scalar_one_or_none()


async def get_all_products(session: AsyncSession) -> Sequence[Product]:
    """Return all products (used to build fuzzy candidate list)."""
    result = await session.execute(select(Product))
    return result.scalars().all()


async def get_all_aliases(session: AsyncSession) -> Sequence[ProductAlias]:
    """Return all confirmed/auto-learned aliases (for fuzzy fallback)."""
    result = await session.execute(
        select(ProductAlias).where(
            ProductAlias.is_confirmed.is_(True)
        )
    )
    return result.scalars().all()


async def get_product_by_id(
    session: AsyncSession, product_id: uuid.UUID
) -> Product | None:
    result = await session.execute(
        select(Product).where(Product.id == product_id)
    )
    return result.scalar_one_or_none()


# ── pgvector search ────────────────────────────────────────────────────────────

async def search_products_by_vector(
    session: AsyncSession,
    embedding: list[float],
    limit: int = 5,
) -> list[tuple[Product, float]]:
    """
    Find products whose name_embedding is closest to the query embedding.
    Returns list of (Product, cosine_distance) sorted by distance ascending.

    Requires pgvector extension and non-null name_embedding column.
    """
    from pgvector.sqlalchemy import Vector
    from sqlalchemy import cast, literal

    embedding_literal = cast(literal(str(embedding)), Vector(len(embedding)))

    result = await session.execute(
        select(
            Product,
            Product.name_embedding.cosine_distance(embedding_literal).label("distance"),
        )
        .where(Product.name_embedding.is_not(None))
        .order_by("distance")
        .limit(limit)
    )
    return [(row.Product, row.distance) for row in result]


# ── Alias creation ─────────────────────────────────────────────────────────────

async def create_alias(
    session: AsyncSession,
    product_id: uuid.UUID,
    alias: str,
    supplier_id: uuid.UUID | None,
    confidence: float,
    source: AliasSource,
    is_confirmed: bool = False,
) -> ProductAlias:
    """Create a new ProductAlias entry."""
    from app.repositories.product_repo import normalise_product_name
    pa = ProductAlias(
        product_id=product_id,
        supplier_id=supplier_id,
        alias=alias,
        normalized_alias=normalise_product_name(alias),
        confidence=confidence,
        is_confirmed=is_confirmed,
        source=source,
    )
    session.add(pa)
    await session.flush()
    return pa


# ── Alias observation tracking ─────────────────────────────────────────────────

async def upsert_alias_observation(
    session: AsyncSession,
    raw_name: str,
    supplier_id: uuid.UUID | None,
    resolved_product_id: uuid.UUID | None,
    resolution_confidence: float | None,
    unit: str | None = None,
) -> AliasObservation:
    """
    Increment the observation counter for a (raw_name, supplier_id) pair.
    Creates a new AliasObservation if first time seen.
    """
    norm = normalise_product_name(raw_name)

    # Try to find existing observation
    stmt = select(AliasObservation).where(
        AliasObservation.normalized_name == norm,
        AliasObservation.supplier_id == supplier_id,
    )
    result = await session.execute(stmt)
    obs = result.scalar_one_or_none()

    now = datetime.now(tz=timezone.utc)

    if obs:
        obs.occurrence_count += 1
        obs.last_seen_at = now
        if resolved_product_id is not None:
            obs.resolved_product_id = resolved_product_id
        if resolution_confidence is not None:
            obs.resolution_confidence = resolution_confidence
        if unit:
            obs.last_seen_unit = unit
    else:
        obs = AliasObservation(
            raw_name=raw_name,
            normalized_name=norm,
            supplier_id=supplier_id,
            resolved_product_id=resolved_product_id,
            occurrence_count=1,
            resolution_confidence=resolution_confidence,
            last_seen_at=now,
            last_seen_unit=unit,
        )
        session.add(obs)

    await session.flush()
    return obs


async def get_alias_observation(
    session: AsyncSession,
    normalised_name: str,
    supplier_id: uuid.UUID | None,
) -> AliasObservation | None:
    result = await session.execute(
        select(AliasObservation).where(
            AliasObservation.normalized_name == normalised_name,
            AliasObservation.supplier_id == supplier_id,
        )
    )
    return result.scalar_one_or_none()
