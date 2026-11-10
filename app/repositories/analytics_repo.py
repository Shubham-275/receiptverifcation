"""
Analytics repository — high-performance SQL aggregation queries for dashboard and reporting.

All metrics are computed inside PostgreSQL (never in Python or LLMs).
"""
from __future__ import annotations

import calendar
import uuid
from datetime import date
from typing import Any

from sqlalchemy import extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product
from app.models.purchase_item import PurchaseItem
from app.models.receipt import Receipt
from app.models.supplier import Supplier
from app.schemas.analytics import (
    CategorySpendingItem,
    MonthlySpendingItem,
    MonthlyVolumeItem,
    ProductHistoryItem,
    ProductMonthlyStats,
    ProductSummaryItem,
    ProductSupplierComparison,
    RecentPurchaseItem,
    SpendingCompositionResponse,
    StoreOverviewResponse,
    SupplierSpendingItem,
)


async def get_store_overview(session: AsyncSession) -> StoreOverviewResponse:
    """Store-wide high-level metrics."""
    spend_stmt = select(
        func.coalesce(func.sum(Receipt.receipt_total), 0).label("total_spend"),
        func.count(Receipt.id).label("total_receipts"),
        func.min(Receipt.receipt_date).label("earliest_date"),
        func.max(Receipt.receipt_date).label("latest_date"),
    )
    spend_res = await session.execute(spend_stmt)
    spend_row = spend_res.one()

    prod_res = await session.execute(select(func.count(Product.id)))
    total_prods = prod_res.scalar_one() or 0

    supp_res = await session.execute(select(func.count(Supplier.id)))
    total_supps = supp_res.scalar_one() or 0

    return StoreOverviewResponse(
        total_spend=float(spend_row.total_spend),
        total_receipts=spend_row.total_receipts,
        total_products=total_prods,
        total_suppliers=total_supps,
        earliest_date=spend_row.earliest_date,
        latest_date=spend_row.latest_date,
    )


async def get_all_products_analytics(session: AsyncSession) -> list[ProductSummaryItem]:
    """Summary of all products with purchase statistics."""
    stmt = (
        select(
            Product.id,
            Product.canonical_name,
            Product.category,
            func.coalesce(func.sum(PurchaseItem.quantity), 0).label("total_quantity"),
            func.coalesce(func.min(PurchaseItem.unit), "units").label("unit"),
            func.coalesce(func.sum(PurchaseItem.line_total), 0).label("total_spend"),
            func.avg(PurchaseItem.unit_price).label("avg_price"),
            func.count(PurchaseItem.id).label("purchase_count"),
            func.max(Receipt.receipt_date).label("last_purchased_date"),
        )
        .outerjoin(PurchaseItem, Product.id == PurchaseItem.product_id)
        .outerjoin(Receipt, PurchaseItem.receipt_id == Receipt.id)
        .group_by(Product.id, Product.canonical_name, Product.category)
        .order_by(func.coalesce(func.sum(PurchaseItem.line_total), 0).desc())
    )
    result = await session.execute(stmt)
    items = []
    for r in result:
        items.append(
            ProductSummaryItem(
                id=r.id,
                canonical_name=r.canonical_name,
                category=r.category,
                total_quantity=float(r.total_quantity),
                unit=r.unit,
                total_spend=float(r.total_spend),
                avg_price=float(r.avg_price) if r.avg_price is not None else None,
                purchase_count=r.purchase_count,
                last_purchased_date=r.last_purchased_date,
            )
        )
    return items


async def get_product_purchase_history(
    session: AsyncSession, product_id: uuid.UUID, limit: int = 50
) -> list[ProductHistoryItem]:
    """Chronological purchase history for a single product."""
    supplier_col = func.coalesce(Supplier.name, Receipt.supplier_raw_name, "Unknown Supplier")

    stmt = (
        select(
            PurchaseItem.receipt_id,
            Receipt.receipt_date,
            supplier_col.label("supplier_name"),
            PurchaseItem.quantity,
            PurchaseItem.unit,
            PurchaseItem.unit_price,
            PurchaseItem.line_total,
        )
        .join(Receipt, PurchaseItem.receipt_id == Receipt.id)
        .outerjoin(Supplier, Receipt.supplier_id == Supplier.id)
        .where(PurchaseItem.product_id == product_id)
        .order_by(Receipt.receipt_date.desc().nullslast(), PurchaseItem.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    history = []
    for r in result:
        history.append(
            ProductHistoryItem(
                receipt_id=r.receipt_id,
                receipt_date=r.receipt_date,
                supplier_name=r.supplier_name,
                quantity=float(r.quantity),
                unit=r.unit,
                unit_price=float(r.unit_price) if r.unit_price is not None else None,
                line_total=float(r.line_total) if r.line_total is not None else None,
            )
        )
    return history


async def get_product_monthly_stats(
    session: AsyncSession, product_id: uuid.UUID, months: int = 9
) -> list[ProductMonthlyStats]:
    """Monthly price and volume trends for a single product (up to N months)."""
    year_col = extract("year", Receipt.receipt_date).label("year")
    month_col = extract("month", Receipt.receipt_date).label("month")

    stmt = (
        select(
            year_col,
            month_col,
            func.sum(PurchaseItem.quantity).label("total_quantity"),
            func.min(PurchaseItem.unit).label("unit"),
            func.avg(PurchaseItem.unit_price).label("avg_price"),
            func.min(PurchaseItem.unit_price).label("min_price"),
            func.max(PurchaseItem.unit_price).label("max_price"),
            func.sum(PurchaseItem.line_total).label("total_spend"),
            func.count(PurchaseItem.id).label("purchase_count"),
        )
        .join(Receipt, PurchaseItem.receipt_id == Receipt.id)
        .where(
            PurchaseItem.product_id == product_id,
            Receipt.receipt_date.is_not(None),
        )
        .group_by(year_col, month_col)
        .order_by(year_col.desc(), month_col.desc())
        .limit(months)
    )
    result = await session.execute(stmt)
    rows = list(result.all())
    rows.reverse()  # chronological order for charts

    stats = []
    for r in rows:
        y = int(r.year)
        m = int(r.month)
        label = f"{calendar.month_abbr[m]} {y}"
        stats.append(
            ProductMonthlyStats(
                year=y,
                month=m,
                month_label=label,
                total_quantity=float(r.total_quantity) if r.total_quantity else 0.0,
                unit=r.unit or "units",
                avg_price=float(r.avg_price) if r.avg_price is not None else None,
                min_price=float(r.min_price) if r.min_price is not None else None,
                max_price=float(r.max_price) if r.max_price is not None else None,
                total_spend=float(r.total_spend) if r.total_spend else 0.0,
                purchase_count=r.purchase_count,
            )
        )
    return stats


async def get_product_suppliers_comparison(
    session: AsyncSession, product_id: uuid.UUID
) -> list[ProductSupplierComparison]:
    """Compare all suppliers for a product."""
    supplier_name_col = func.coalesce(Supplier.name, Receipt.supplier_raw_name, "Unknown Supplier")

    stmt = (
        select(
            Supplier.id.label("supplier_id"),
            supplier_name_col.label("supplier_name"),
            func.avg(PurchaseItem.unit_price).label("avg_price"),
            func.min(PurchaseItem.unit_price).label("min_price"),
            func.max(PurchaseItem.unit_price).label("max_price"),
            func.sum(PurchaseItem.quantity).label("total_quantity"),
            func.min(PurchaseItem.unit).label("unit"),
            func.sum(PurchaseItem.line_total).label("total_spend"),
            func.count(PurchaseItem.id).label("purchase_count"),
        )
        .join(Receipt, PurchaseItem.receipt_id == Receipt.id)
        .outerjoin(Supplier, Receipt.supplier_id == Supplier.id)
        .where(PurchaseItem.product_id == product_id)
        .group_by(Supplier.id, supplier_name_col)
        .order_by(func.avg(PurchaseItem.unit_price).asc().nullslast())
    )
    result = await session.execute(stmt)
    comps = []
    for r in result:
        comps.append(
            ProductSupplierComparison(
                supplier_id=r.supplier_id,
                supplier_name=r.supplier_name,
                avg_price=float(r.avg_price) if r.avg_price is not None else None,
                min_price=float(r.min_price) if r.min_price is not None else None,
                max_price=float(r.max_price) if r.max_price is not None else None,
                total_quantity=float(r.total_quantity) if r.total_quantity else 0.0,
                unit=r.unit or "units",
                total_spend=float(r.total_spend) if r.total_spend else 0.0,
                purchase_count=r.purchase_count,
            )
        )
    return comps


async def get_monthly_spending(
    session: AsyncSession, months: int = 12
) -> list[MonthlySpendingItem]:
    """Store-wide monthly spending totals."""
    year_col = extract("year", Receipt.receipt_date).label("year")
    month_col = extract("month", Receipt.receipt_date).label("month")

    stmt = (
        select(
            year_col,
            month_col,
            func.sum(Receipt.receipt_total).label("total_spending"),
            func.count(Receipt.id).label("receipt_count"),
        )
        .where(Receipt.receipt_date.is_not(None))
        .group_by(year_col, month_col)
        .order_by(year_col.desc(), month_col.desc())
        .limit(months)
    )
    result = await session.execute(stmt)
    rows = list(result.all())
    rows.reverse()

    spending = []
    for r in rows:
        y = int(r.year)
        m = int(r.month)
        spending.append(
            MonthlySpendingItem(
                year=y,
                month=m,
                month_label=f"{calendar.month_abbr[m]} {y}",
                total_spending=float(r.total_spending) if r.total_spending else 0.0,
                receipt_count=r.receipt_count,
            )
        )
    return spending


async def get_spending_composition(
    session: AsyncSession,
) -> SpendingCompositionResponse:
    """Spending split by category and supplier, with monthly item volume."""
    totals = await session.execute(
        select(
            func.coalesce(func.sum(Receipt.receipt_total), 0).label("total_spend"),
            func.count(Receipt.id).label("total_receipts"),
        )
    )
    totals_row = totals.one()
    total_spend = float(totals_row.total_spend)
    total_receipts = int(totals_row.total_receipts)

    items_total = await session.execute(select(func.count(PurchaseItem.id)))
    total_line_items = int(items_total.scalar_one() or 0)

    category_col = func.coalesce(Product.category, "Uncategorised").label("category")
    category_stmt = (
        select(
            category_col,
            func.coalesce(func.sum(PurchaseItem.line_total), 0).label("total_spend"),
            func.count(PurchaseItem.id).label("line_count"),
            func.count(func.distinct(Product.id)).label("product_count"),
        )
        .select_from(PurchaseItem)
        .outerjoin(Product, PurchaseItem.product_id == Product.id)
        .group_by(category_col)
        .order_by(func.coalesce(func.sum(PurchaseItem.line_total), 0).desc())
    )
    category_rows = (await session.execute(category_stmt)).all()
    categories = [
        CategorySpendingItem(
            category=row.category,
            total_spend=float(row.total_spend),
            line_count=int(row.line_count),
            product_count=int(row.product_count),
        )
        for row in category_rows
    ]

    supplier_col = func.coalesce(
        Supplier.name, Receipt.supplier_raw_name, "Unknown supplier"
    ).label("supplier_name")
    supplier_stmt = (
        select(
            Supplier.id.label("supplier_id"),
            supplier_col,
            func.coalesce(func.sum(Receipt.receipt_total), 0).label("total_spend"),
            func.count(Receipt.id).label("invoice_count"),
        )
        .select_from(Receipt)
        .outerjoin(Supplier, Receipt.supplier_id == Supplier.id)
        .group_by(Supplier.id, supplier_col)
        .order_by(func.coalesce(func.sum(Receipt.receipt_total), 0).desc())
    )
    supplier_rows = (await session.execute(supplier_stmt)).all()
    suppliers = [
        SupplierSpendingItem(
            supplier_id=row.supplier_id,
            supplier_name=row.supplier_name,
            total_spend=float(row.total_spend),
            invoice_count=int(row.invoice_count),
        )
        for row in supplier_rows
    ]

    monthly_spend = await get_monthly_spending(session, months=12)

    year_col = extract("year", Receipt.receipt_date).label("year")
    month_col = extract("month", Receipt.receipt_date).label("month")
    volume_stmt = (
        select(
            year_col,
            month_col,
            func.count(PurchaseItem.id).label("item_count"),
        )
        .join(Receipt, PurchaseItem.receipt_id == Receipt.id)
        .where(Receipt.receipt_date.is_not(None))
        .group_by(year_col, month_col)
        .order_by(year_col.desc(), month_col.desc())
        .limit(12)
    )
    volume_rows = (await session.execute(volume_stmt)).all()
    item_counts = {(int(row.year), int(row.month)): int(row.item_count) for row in volume_rows}

    monthly_volume = [
        MonthlyVolumeItem(
            year=row.year,
            month=row.month,
            month_label=row.month_label,
            total_spending=row.total_spending,
            receipt_count=row.receipt_count,
            item_count=item_counts.get((row.year, row.month), 0),
        )
        for row in monthly_spend
    ]

    return SpendingCompositionResponse(
        total_spend=total_spend,
        total_receipts=total_receipts,
        total_line_items=total_line_items,
        average_invoice=total_spend / total_receipts if total_receipts else 0.0,
        average_items_per_invoice=(
            total_line_items / total_receipts if total_receipts else 0.0
        ),
        categories=categories,
        suppliers=suppliers,
        monthly_volume=monthly_volume,
    )


async def get_recent_purchases(
    session: AsyncSession, limit: int = 20
) -> list[RecentPurchaseItem]:
    """Recent individual purchases across all products."""
    supplier_col = func.coalesce(Supplier.name, Receipt.supplier_raw_name, "Unknown Supplier")
    prod_name_col = func.coalesce(Product.canonical_name, PurchaseItem.raw_name)

    stmt = (
        select(
            PurchaseItem.receipt_id,
            Receipt.receipt_date,
            supplier_col.label("supplier_name"),
            prod_name_col.label("product_name"),
            PurchaseItem.quantity,
            PurchaseItem.unit,
            PurchaseItem.unit_price,
            PurchaseItem.line_total,
        )
        .join(Receipt, PurchaseItem.receipt_id == Receipt.id)
        .outerjoin(Supplier, Receipt.supplier_id == Supplier.id)
        .outerjoin(Product, PurchaseItem.product_id == Product.id)
        .order_by(Receipt.receipt_date.desc().nullslast(), PurchaseItem.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    purchases = []
    for r in result:
        purchases.append(
            RecentPurchaseItem(
                receipt_id=r.receipt_id,
                receipt_date=r.receipt_date,
                supplier_name=r.supplier_name,
                product_name=r.product_name,
                quantity=float(r.quantity),
                unit=r.unit,
                unit_price=float(r.unit_price) if r.unit_price is not None else None,
                line_total=float(r.line_total) if r.line_total is not None else None,
            )
        )
    return purchases
