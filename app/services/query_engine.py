"""
Query Engine — routes and executes natural-language queries.

Adheres strictly to plan.md principles:
1. RAG is NOT the default mechanism for every query.
2. PostgreSQL is the source of truth and performs all aggregation (SUM, AVG, MIN, MAX).
3. The LLM receives the small aggregated result, never thousands of raw rows.
4. Vector retrieval (pgvector) is used only for semantic/unstructured receipt search.
"""
from __future__ import annotations

import enum
import re
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.logging_config import get_logger
from app.models.product import Product
from app.models.purchase_item import PurchaseItem
from app.models.receipt import Receipt
from app.models.supplier import Supplier
from app.repositories import product_repo, receipt_repo
from app.schemas.query import QueryResponse
from app.services.ingestion import receipt_to_detail_response
from app.services.retrieval import search_receipts_semantic

log = get_logger(__name__)


class RouteType(str, enum.Enum):
    sql = "sql"
    rag = "rag"
    sql_and_rag = "sql_and_rag"


def classify_route(query: str) -> RouteType:
    """
    Classify whether a user query requires SQL, RAG (vector search), or both.
    """
    q = query.lower()

    # RAG indicator phrases
    rag_patterns = [
        r"\bfind\b[\w\s-]*\b(?:receipt|bill)s?\b",
        r"\bwhich\b[\w\s-]*\b(?:receipt|bill)s?\b",
        r"\bshow\b[\w\s-]*\b(?:receipt|bill)s?\b",
        r"\b(?:receipt|bill)s?\s+where\b",
        r"\bbought that\b",
        r"\bsimilar to\b",
        r"\baround ₹?\d+\b",
    ]
    is_rag = any(re.search(pat, q) for pat in rag_patterns)

    # SQL indicator phrases (numerical aggregation, price checks, supplier comparisons)
    sql_patterns = [
        r"\bhow much\b",
        r"\bhow many\b",
        r"\baverage\b",
        r"\bavg\b",
        r"\bcheapest\b",
        r"\bcheaper\b",
        r"\bmost expensive\b",
        r"\bhighest price\b",
        r"\blowest price\b",
        r"\btotal (?:spent|spending|cost|purchase)\b",
        r"\bspent\b",
        r"\bspending\b",
        r"\bprice trend\b",
        r"\bprice increased\b",
        r"\bprice decreased\b",
    ]
    is_sql = any(re.search(pat, q) for pat in sql_patterns)

    if is_rag and is_sql:
        return RouteType.sql_and_rag
    elif is_rag:
        return RouteType.rag
    else:
        # Default to SQL for analytical and purchasing questions
        return RouteType.sql


# ── SQL Execution Helpers (PostgreSQL aggregates) ──────────────────────────────

async def execute_product_quantity_and_spend(
    session: AsyncSession,
    product_query: str,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    """Calculate SUM(quantity), SUM(line_total), AVG(unit_price) inside PostgreSQL."""
    norm_query = product_repo.normalise_product_name(product_query)

    stmt = (
        select(
            func.sum(PurchaseItem.quantity).label("total_quantity"),
            func.min(PurchaseItem.unit).label("unit"),
            func.sum(PurchaseItem.line_total).label("total_spend"),
            func.avg(PurchaseItem.unit_price).label("avg_unit_price"),
            func.min(PurchaseItem.unit_price).label("min_unit_price"),
            func.max(PurchaseItem.unit_price).label("max_unit_price"),
            func.count(PurchaseItem.id).label("purchase_count"),
        )
        .join(Receipt, PurchaseItem.receipt_id == Receipt.id)
        .outerjoin(Product, PurchaseItem.product_id == Product.id)
        .where(
            func.lower(PurchaseItem.raw_name).contains(norm_query)
            | func.lower(Product.normalized_name).contains(norm_query)
            | func.lower(Product.canonical_name).contains(norm_query)
        )
    )

    if date_from:
        stmt = stmt.where(Receipt.receipt_date >= date_from)
    if date_to:
        stmt = stmt.where(Receipt.receipt_date <= date_to)

    result = await session.execute(stmt)
    row = result.one()

    return {
        "product_query": product_query,
        "total_quantity": float(row.total_quantity) if row.total_quantity is not None else 0.0,
        "unit": row.unit or "units",
        "total_spend": float(row.total_spend) if row.total_spend is not None else 0.0,
        "avg_unit_price": float(row.avg_unit_price) if row.avg_unit_price is not None else None,
        "min_unit_price": float(row.min_unit_price) if row.min_unit_price is not None else None,
        "max_unit_price": float(row.max_unit_price) if row.max_unit_price is not None else None,
        "purchase_count": row.purchase_count or 0,
    }


async def execute_supplier_comparison(
    session: AsyncSession,
    product_query: str,
) -> list[dict[str, Any]]:
    """Compare suppliers for a product inside PostgreSQL."""
    norm_query = product_repo.normalise_product_name(product_query)

    supplier_col = func.coalesce(Supplier.name, Receipt.supplier_raw_name, "Unknown Supplier")

    stmt = (
        select(
            supplier_col.label("supplier_name"),
            func.avg(PurchaseItem.unit_price).label("avg_price"),
            func.min(PurchaseItem.unit_price).label("min_price"),
            func.max(PurchaseItem.unit_price).label("max_price"),
            func.sum(PurchaseItem.quantity).label("total_quantity"),
            func.min(PurchaseItem.unit).label("unit"),
            func.count(PurchaseItem.id).label("purchase_count"),
        )
        .join(Receipt, PurchaseItem.receipt_id == Receipt.id)
        .outerjoin(Supplier, Receipt.supplier_id == Supplier.id)
        .outerjoin(Product, PurchaseItem.product_id == Product.id)
        .where(
            func.lower(PurchaseItem.raw_name).contains(norm_query)
            | func.lower(Product.normalized_name).contains(norm_query)
            | func.lower(Product.canonical_name).contains(norm_query)
        )
        .group_by(supplier_col)
        .order_by(func.avg(PurchaseItem.unit_price).asc())
    )

    result = await session.execute(stmt)
    suppliers = []
    for row in result:
        suppliers.append({
            "supplier_name": row.supplier_name,
            "avg_price": float(row.avg_price) if row.avg_price is not None else None,
            "min_price": float(row.min_price) if row.min_price is not None else None,
            "max_price": float(row.max_price) if row.max_price is not None else None,
            "total_quantity": float(row.total_quantity) if row.total_quantity is not None else 0.0,
            "unit": row.unit or "units",
            "purchase_count": row.purchase_count or 0,
        })
    return suppliers


# ── Natural Language Answer Generator ──────────────────────────────────────────

def generate_natural_answer(
    query: str,
    route: RouteType,
    data: dict[str, Any] | None,
    receipts: list[Receipt],
) -> str:
    """Format a crisp, user-friendly natural answer from structured results."""
    q = query.lower()

    if route == RouteType.rag:
        if not receipts:
            return "No matching receipts found for your search."
        r = receipts[0]
        date_str = r.receipt_date.isoformat() if r.receipt_date else "unknown date"
        supplier_str = r.supplier_raw_name or "Unknown supplier"
        total_str = f"₹{r.receipt_total:,.2f}" if r.receipt_total else "unknown total"
        item_names = [it.raw_name for it in r.purchase_items[:5]]
        items_preview = ", ".join(item_names) if item_names else "items"
        return (
            f"Found matching receipt from {supplier_str} on {date_str} "
            f"(Total: {total_str}). Items include: {items_preview}."
        )

    # SQL or SQL + RAG
    if data:
        # Supplier comparison query
        if "supplier_comparison" in data:
            comps = data["supplier_comparison"]
            if not comps:
                return f"No purchase records found for '{data.get('product_query', 'this product')}'."
            cheapest = comps[0]
            price_str = f"₹{cheapest['avg_price']:.2f}" if cheapest['avg_price'] else "N/A"
            unit_str = f"/{cheapest['unit']}" if cheapest.get('unit') else ""
            msg = f"{cheapest['supplier_name']} gave you the cheapest price at {price_str}{unit_str}."
            if len(comps) > 1:
                others = ", ".join(
                    f"{c['supplier_name']} (₹{c['avg_price']:.2f})" for c in comps[1:] if c['avg_price']
                )
                if others:
                    msg += f" Other suppliers: {others}."
            return msg

        # Quantity / spend / avg price query
        p_name = data.get("product_query", "this product")
        total_qty = data.get("total_quantity", 0.0)
        unit = data.get("unit", "units")
        total_spend = data.get("total_spend", 0.0)
        avg_price = data.get("avg_unit_price")
        p_count = data.get("purchase_count", 0)

        if p_count == 0 or total_qty == 0.0:
            return f"You have no recorded purchases for '{p_name}' in the requested period."

        if "average" in q or "avg" in q:
            price_str = f"₹{avg_price:.2f}" if avg_price is not None else "N/A"
            return (
                f"Your average buying price for {p_name} was {price_str} per {unit} "
                f"across {p_count} purchases (total {total_qty:g} {unit})."
            )

        if "how much did i spend" in q or "spending" in q:
            return (
                f"You spent a total of ₹{total_spend:,.2f} on {p_name} "
                f"({total_qty:g} {unit} across {p_count} purchases)."
            )

        # Default quantity answer
        avg_str = f" (average ₹{avg_price:.2f}/{unit})" if avg_price else ""
        return (
            f"You bought {total_qty:g} {unit} of {p_name} for a total of "
            f"₹{total_spend:,.2f}{avg_str} across {p_count} purchase(s)."
        )

    return "Query processed successfully."


# ── Query Dispatcher ───────────────────────────────────────────────────────────

def _extract_product_name_from_query(query: str) -> str:
    """Extract candidate product name from common inquiry patterns."""
    q = query.strip()
    # "how much rice did I buy" -> rice
    m = re.search(r"how much ([\w\s-]+?)(?:\s+did|\s+have|\s+last|\s+in|\?|$)", q, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # "average buying price for Harpic" -> Harpic
    m = re.search(r"(?:for|of)\s+([\w\s-]+?)(?:\s+in|\s+over|\s+last|\?|$)", q, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # "cheapest Parle-G" -> Parle-G
    m = re.search(r"(?:cheapest|most expensive)\s+([\w\s-]+?)(?:\s+in|\s+from|\?|$)", q, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # Fallback to cleaning query
    cleaned = re.sub(r"[^\w\s-]", "", q)
    return cleaned.strip()


async def process_user_query(
    session: AsyncSession, query_str: str
) -> QueryResponse:
    """Execute the end-to-end query workflow."""
    t0 = time.perf_counter()
    route = classify_route(query_str)
    log.info("query_classified", query=query_str, route=route.value)

    structured_data: dict[str, Any] | None = None
    matched_receipts: list[Receipt] = []

    if route in (RouteType.sql, RouteType.sql_and_rag):
        product_name = _extract_product_name_from_query(query_str)
        q_lower = query_str.lower()

        if "cheapest" in q_lower or "supplier" in q_lower:
            comps = await execute_supplier_comparison(session, product_name)
            structured_data = {
                "product_query": product_name,
                "supplier_comparison": comps,
            }
        else:
            stats = await execute_product_quantity_and_spend(session, product_name)
            structured_data = stats

    if route in (RouteType.rag, RouteType.sql_and_rag):
        semantic_results = await search_receipts_semantic(
            session, query_str, limit=5
        )
        matched_receipts = [r for r, _dist in semantic_results]

    answer = generate_natural_answer(query_str, route, structured_data, matched_receipts)
    execution_time_ms = (time.perf_counter() - t0) * 1000

    receipt_details = [receipt_to_detail_response(r) for r in matched_receipts]

    return QueryResponse(
        query=query_str,
        route=route.value,
        answer=answer,
        structured_data=structured_data,
        receipts=receipt_details,
        execution_time_ms=round(execution_time_ms, 2),
    )
