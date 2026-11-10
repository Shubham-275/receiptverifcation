from __future__ import annotations

import uuid
from datetime import date
from pydantic import BaseModel, ConfigDict


class StoreOverviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_spend: float
    total_receipts: int
    total_products: int
    total_suppliers: int
    earliest_date: date | None = None
    latest_date: date | None = None


class ProductSummaryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    canonical_name: str
    category: str | None = None
    total_quantity: float
    unit: str
    total_spend: float
    avg_price: float | None = None
    purchase_count: int
    last_purchased_date: date | None = None


class ProductHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    receipt_id: uuid.UUID
    receipt_date: date | None = None
    supplier_name: str
    quantity: float
    unit: str
    unit_price: float | None = None
    line_total: float | None = None


class ProductMonthlyStats(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    year: int
    month: int
    month_label: str
    total_quantity: float
    unit: str
    avg_price: float | None = None
    min_price: float | None = None
    max_price: float | None = None
    total_spend: float
    purchase_count: int


class ProductSupplierComparison(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    supplier_id: uuid.UUID | None = None
    supplier_name: str
    avg_price: float | None = None
    min_price: float | None = None
    max_price: float | None = None
    total_quantity: float
    unit: str
    total_spend: float
    purchase_count: int


class MonthlySpendingItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    year: int
    month: int
    month_label: str
    total_spending: float
    receipt_count: int


class RecentPurchaseItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    receipt_id: uuid.UUID
    receipt_date: date | None = None
    supplier_name: str
    product_name: str
    quantity: float
    unit: str
    unit_price: float | None = None
    line_total: float | None = None


class CategorySpendingItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    category: str
    total_spend: float
    line_count: int
    product_count: int


class SupplierSpendingItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    supplier_id: uuid.UUID | None = None
    supplier_name: str
    total_spend: float
    invoice_count: int


class MonthlyVolumeItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    year: int
    month: int
    month_label: str
    total_spending: float
    receipt_count: int
    item_count: int


class SpendingCompositionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total_spend: float
    total_receipts: int
    total_line_items: int
    average_invoice: float
    average_items_per_invoice: float
    categories: list[CategorySpendingItem]
    suppliers: list[SupplierSpendingItem]
    monthly_volume: list[MonthlyVolumeItem]
