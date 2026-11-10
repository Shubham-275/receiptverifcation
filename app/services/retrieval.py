"""
Semantic and hybrid retrieval service using PostgreSQL + pgvector.

Core principle:
- PostgreSQL is the source of truth.
- Vector search is used for semantic queries where exact matching/SQL is insufficient.
- Numerical aggregations are never run over vector retrieval.
"""
from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models.product import Product
from app.models.receipt import Receipt
from app.repositories import product_repo, receipt_repo
from app.services.embedding import embed_text

log = get_logger(__name__)


async def search_receipts_semantic(
    session: AsyncSession,
    query: str,
    limit: int = 5,
    max_distance: float = 0.8,
) -> list[tuple[Receipt, float]]:
    """
    Semantic receipt retrieval using pgvector cosine distance.

    Args:
        session: Active async database session.
        query: Natural language query (e.g. "biscuit for around ₹10", "cleaning supplies").
        limit: Max number of receipts to return.
        max_distance: Max cosine distance threshold (1.0 - similarity).

    Returns:
        List of (Receipt, distance) tuples ordered by semantic closeness.
    """
    try:
        query_vector = await embed_text(query)
    except Exception as exc:
        log.warning("semantic_query_embed_failed", error=str(exc), query=query)
        return []

    results = await receipt_repo.search_receipts_by_vector(
        session, query_vector, limit=limit
    )

    filtered = [
        (receipt, dist) for receipt, dist in results if dist <= max_distance
    ]
    log.info(
        "semantic_receipt_search_completed",
        query=query,
        raw_count=len(results),
        filtered_count=len(filtered),
    )
    return filtered


async def search_products_semantic(
    session: AsyncSession,
    query: str,
    limit: int = 5,
    max_distance: float = 0.75,
) -> list[tuple[Product, float]]:
    """
    Semantic product lookup using pgvector cosine distance against canonical product embeddings.
    """
    try:
        query_vector = await embed_text(query)
    except Exception as exc:
        log.warning("product_semantic_embed_failed", error=str(exc), query=query)
        return []

    results = await product_repo.search_products_by_vector(
        session, query_vector, limit=limit
    )
    return [(prod, dist) for prod, dist in results if dist <= max_distance]


async def hybrid_receipt_search(
    session: AsyncSession,
    query: str,
    limit: int = 5,
) -> list[Receipt]:
    """
    Hybrid retrieval:
    1. Check for exact or normalized supplier / product match.
    2. Run semantic vector search.
    3. Merge and deduplicate results.
    """
    norm = product_repo.normalise_product_name(query)
    seen_ids: set[uuid.UUID] = set()
    ordered_results: list[Receipt] = []

    # 1. Exact supplier lookup
    supplier = await product_repo.find_supplier_by_name(session, query)
    if supplier:
        supplier_receipts, _ = await receipt_repo.list_receipts(
            session, supplier_id=supplier.id, limit=limit
        )
        for r in supplier_receipts:
            if r.id not in seen_ids:
                seen_ids.add(r.id)
                ordered_results.append(r)

    # 2. Semantic vector search
    vector_results = await search_receipts_semantic(
        session, query, limit=limit
    )
    for r, _dist in vector_results:
        if r.id not in seen_ids:
            seen_ids.add(r.id)
            ordered_results.append(r)

    return ordered_results[:limit]
