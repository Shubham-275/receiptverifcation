from __future__ import annotations

import uuid
from datetime import date, datetime
from pydantic import BaseModel, ConfigDict


class ReceiptItemDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    receipt_id: uuid.UUID
    raw_name: str
    product_id: uuid.UUID | None = None
    product_name: str | None = None
    quantity: float
    unit: str
    unit_price: float | None = None
    line_total: float | None = None
    currency: str = "INR"
    resolution_method: str
    resolution_confidence: float | None = None
    notes: str | None = None


class ReceiptDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    supplier_id: uuid.UUID | None = None
    supplier_name: str | None = None
    receipt_date: date | None = None
    invoice_number: str | None = None
    receipt_total: float | None = None
    currency: str = "INR"
    status: str
    extraction_confidence: float | None = None
    classifier_confidence: float | None = None
    image_path: str | None = None
    validation_notes: str | None = None
    item_count: int = 0
    items: list[ReceiptItemDetail] = []
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ReceiptListResponse(BaseModel):
    total: int
    receipts: list[ReceiptDetailResponse]


class ExtractedItemResponse(BaseModel):
    raw_name: str
    quantity: float
    unit: str
    unit_price: float | None = None
    line_total: float | None = None


class ExtractResponse(BaseModel):
    """Full extract + validate pipeline result."""
    classifier_confidence: float
    supplier: str | None = None
    date: str | None = None
    invoice_number: str | None = None
    receipt_total: float | None = None
    currency: str = "INR"
    items: list[ExtractedItemResponse] = []
    item_count: int = 0
    extraction_confidence: float = 1.0
    extraction_notes: str | None = None
    validation_summary: str = ""
    total_match: bool = True
    item_math_failures: int = 0
    is_valid: bool = True


class BatchReceiptItemResult(BaseModel):
    filename: str
    success: bool
    status_code: int = 200
    receipt: ReceiptDetailResponse | None = None
    error: str | None = None
    detail: str | None = None


class BatchUploadResponse(BaseModel):
    total_files: int
    successful_count: int
    failed_count: int
    total_spend: float = 0.0
    total_items: int = 0
    results: list[BatchReceiptItemResult]


class BatchExtractItemResult(BaseModel):
    filename: str
    success: bool
    status_code: int = 200
    extraction: ExtractResponse | None = None
    error: str | None = None
    detail: str | None = None


class BatchExtractResponse(BaseModel):
    total_files: int
    successful_count: int
    failed_count: int
    total_spend: float = 0.0
    total_items: int = 0
    results: list[BatchExtractItemResult]

