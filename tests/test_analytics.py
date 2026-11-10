"""
Tests for Phase 10 — Analytics Backend.

Covers:
  - analytics_repo SQL functions (overview, product summary, history, monthly stats, suppliers, spending)
  - All /api/v1/analytics endpoints
"""
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


class TestAnalyticsEndpoints:

    def test_get_store_overview(self, client):
        mock_data = StoreOverviewResponse(
            total_spend=4280.0,
            total_receipts=3,
            total_products=6,
            total_suppliers=2,
            earliest_date=date(2026, 1, 15),
            latest_date=date(2026, 9, 29),
        )
        with patch("app.repositories.analytics_repo.get_store_overview", AsyncMock(return_value=mock_data)):
            resp = client.get("/api/v1/analytics/overview")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_spend"] == 4280.0
        assert data["total_receipts"] == 3
        assert data["total_products"] == 6

    def test_get_products_analytics(self, client):
        p_id = uuid.uuid4()
        mock_items = [
            ProductSummaryItem(
                id=p_id,
                canonical_name="Rice",
                category="grocery",
                total_quantity=25.0,
                unit="kg",
                total_spend=1850.0,
                avg_price=74.0,
                purchase_count=1,
                last_purchased_date=date(2026, 9, 29),
            )
        ]
        with patch("app.repositories.analytics_repo.get_all_products_analytics", AsyncMock(return_value=mock_items)):
            resp = client.get("/api/v1/analytics/products")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["canonical_name"] == "Rice"
        assert data[0]["total_spend"] == 1850.0

    def test_get_product_history(self, client):
        p_id = uuid.uuid4()
        r_id = uuid.uuid4()
        mock_history = [
            ProductHistoryItem(
                receipt_id=r_id,
                receipt_date=date(2026, 9, 29),
                supplier_name="Sharma Traders",
                quantity=25.0,
                unit="kg",
                unit_price=74.0,
                line_total=1850.0,
            )
        ]
        with patch("app.repositories.analytics_repo.get_product_purchase_history", AsyncMock(return_value=mock_history)):
            resp = client.get(f"/api/v1/analytics/products/{p_id}/history")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["supplier_name"] == "Sharma Traders"
        assert data[0]["quantity"] == 25.0

    def test_get_product_monthly_stats(self, client):
        p_id = uuid.uuid4()
        mock_stats = [
            ProductMonthlyStats(
                year=2026,
                month=9,
                month_label="Sep 2026",
                total_quantity=25.0,
                unit="kg",
                avg_price=74.0,
                min_price=74.0,
                max_price=74.0,
                total_spend=1850.0,
                purchase_count=1,
            )
        ]
        with patch("app.repositories.analytics_repo.get_product_monthly_stats", AsyncMock(return_value=mock_stats)):
            resp = client.get(f"/api/v1/analytics/products/{p_id}/monthly-stats?months=9")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["month_label"] == "Sep 2026"
        assert data[0]["avg_price"] == 74.0

    def test_get_product_suppliers_comparison(self, client):
        p_id = uuid.uuid4()
        mock_comps = [
            ProductSupplierComparison(
                supplier_id=uuid.uuid4(),
                supplier_name="Sharma Traders",
                avg_price=74.0,
                min_price=74.0,
                max_price=74.0,
                total_quantity=25.0,
                unit="kg",
                total_spend=1850.0,
                purchase_count=1,
            )
        ]
        with patch("app.repositories.analytics_repo.get_product_suppliers_comparison", AsyncMock(return_value=mock_comps)):
            resp = client.get(f"/api/v1/analytics/products/{p_id}/suppliers")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["supplier_name"] == "Sharma Traders"

    def test_get_monthly_spending(self, client):
        mock_spending = [
            MonthlySpendingItem(
                year=2026,
                month=9,
                month_label="Sep 2026",
                total_spending=4280.0,
                receipt_count=2,
            )
        ]
        with patch("app.repositories.analytics_repo.get_monthly_spending", AsyncMock(return_value=mock_spending)):
            resp = client.get("/api/v1/analytics/spending/monthly")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["total_spending"] == 4280.0

    def test_get_spending_composition(self, client):
        supplier_id = uuid.uuid4()
        mock_composition = SpendingCompositionResponse(
            total_spend=4280.0,
            total_receipts=3,
            total_line_items=17,
            average_invoice=1426.67,
            average_items_per_invoice=5.67,
            categories=[
                CategorySpendingItem(
                    category="grocery",
                    total_spend=3000.0,
                    line_count=12,
                    product_count=3,
                )
            ],
            suppliers=[
                SupplierSpendingItem(
                    supplier_id=supplier_id,
                    supplier_name="Sharma Traders",
                    total_spend=2500.0,
                    invoice_count=2,
                )
            ],
            monthly_volume=[
                MonthlyVolumeItem(
                    year=2026,
                    month=9,
                    month_label="Sep 2026",
                    total_spending=4280.0,
                    receipt_count=3,
                    item_count=17,
                )
            ],
        )
        with patch("app.repositories.analytics_repo.get_spending_composition", AsyncMock(return_value=mock_composition)):
            resp = client.get("/api/v1/analytics/spending/composition")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_line_items"] == 17
        assert data["average_items_per_invoice"] == 5.67
        assert data["categories"][0]["category"] == "grocery"
        assert data["suppliers"][0]["supplier_name"] == "Sharma Traders"
        assert data["monthly_volume"][0]["item_count"] == 17

    def test_get_recent_purchases(self, client):
        mock_recent = [
            RecentPurchaseItem(
                receipt_id=uuid.uuid4(),
                receipt_date=date(2026, 9, 29),
                supplier_name="Sharma Traders",
                product_name="Rice",
                quantity=25.0,
                unit="kg",
                unit_price=74.0,
                line_total=1850.0,
            )
        ]
        with patch("app.repositories.analytics_repo.get_recent_purchases", AsyncMock(return_value=mock_recent)):
            resp = client.get("/api/v1/analytics/purchases/recent?limit=10")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["product_name"] == "Rice"
