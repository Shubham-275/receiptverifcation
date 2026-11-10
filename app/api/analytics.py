"""
Analytics API endpoints.

GET /api/v1/analytics/overview
GET /api/v1/analytics/products
GET /api/v1/analytics/products/{product_id}/history
GET /api/v1/analytics/products/{product_id}/monthly-stats
GET /api/v1/analytics/products/{product_id}/suppliers
GET /api/v1/analytics/spending/monthly
GET /api/v1/analytics/spending/composition
GET /api/v1/analytics/purchases/recent
"""
from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_dep
from app.repositories import analytics_repo
from app.schemas.analytics import (
    MonthlySpendingItem,
    ProductHistoryItem,
    ProductMonthlyStats,
    ProductSummaryItem,
    ProductSupplierComparison,
    RecentPurchaseItem,
    SpendingCompositionResponse,
    StoreOverviewResponse,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get(
    "/overview",
    response_model=StoreOverviewResponse,
    summary="Get storewide purchasing overview",
    description="Total spend, receipt count, product count, supplier count, and date range.",
)
async def get_overview(
    db: AsyncSession = Depends(get_db_dep),
) -> StoreOverviewResponse:
    return await analytics_repo.get_store_overview(db)


@router.get(
    "/products",
    response_model=List[ProductSummaryItem],
    summary="List all products with aggregate statistics",
    description="Total spend, total quantity, avg price, and purchase count per product.",
)
async def get_products_analytics(
    db: AsyncSession = Depends(get_db_dep),
) -> List[ProductSummaryItem]:
    return await analytics_repo.get_all_products_analytics(db)


@router.get(
    "/products/{product_id}/history",
    response_model=List[ProductHistoryItem],
    summary="Get purchase history for a specific product",
    description="Chronological purchase entries with date, supplier, quantity, price.",
)
async def get_product_history(
    product_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_dep),
) -> List[ProductHistoryItem]:
    return await analytics_repo.get_product_purchase_history(db, product_id, limit=limit)


@router.get(
    "/products/{product_id}/monthly-stats",
    response_model=List[ProductMonthlyStats],
    summary="Get monthly volume and price statistics for a product",
    description="Used for 9-month buying price and quantity trend visualization.",
)
async def get_product_monthly_stats(
    product_id: uuid.UUID,
    months: int = Query(9, ge=1, le=36),
    db: AsyncSession = Depends(get_db_dep),
) -> List[ProductMonthlyStats]:
    return await analytics_repo.get_product_monthly_stats(db, product_id, months=months)


@router.get(
    "/products/{product_id}/suppliers",
    response_model=List[ProductSupplierComparison],
    summary="Compare supplier prices for a specific product",
    description="Avg, min, max prices and total volume purchased per supplier.",
)
async def get_product_suppliers(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_dep),
) -> List[ProductSupplierComparison]:
    return await analytics_repo.get_product_suppliers_comparison(db, product_id)


@router.get(
    "/spending/monthly",
    response_model=List[MonthlySpendingItem],
    summary="Get storewide monthly spending trend",
    description="Total purchasing spend grouped by month for financial monitoring.",
)
async def get_monthly_spending(
    months: int = Query(12, ge=1, le=36),
    db: AsyncSession = Depends(get_db_dep),
) -> List[MonthlySpendingItem]:
    return await analytics_repo.get_monthly_spending(db, months=months)


@router.get(
    "/spending/composition",
    response_model=SpendingCompositionResponse,
    summary="Get spending split by category and supplier",
    description=(
        "Category and supplier concentration, monthly spend and item volume, "
        "and basket ratios for the analytics workspace."
    ),
)
async def get_spending_composition(
    db: AsyncSession = Depends(get_db_dep),
) -> SpendingCompositionResponse:
    return await analytics_repo.get_spending_composition(db)


@router.get(
    "/purchases/recent",
    response_model=List[RecentPurchaseItem],
    summary="Get most recent line-item purchases across all receipts",
    description="Recent purchases feed for dashboard activity stream.",
)
async def get_recent_purchases(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_dep),
) -> List[RecentPurchaseItem]:
    return await analytics_repo.get_recent_purchases(db, limit=limit)
