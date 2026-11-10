"""
Pydantic schemas for receipt extraction output.

These are the "raw" schemas — data as Gemini returns it, before
validation and normalization (Phase 5). They are deliberately lenient
(many nullable fields) because OCR/LLM output can be incomplete.

Used by:
  - extractor.py   (production of data)
  - normalizer.py  (consumption + validation)
  - API responses  (serialized back to callers)
"""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Line item ─────────────────────────────────────────────────────────────────

class ExtractedItem(BaseModel):
    """A single line item as extracted from the receipt image."""

    raw_name: str = Field(
        ...,
        min_length=1,
        description="Product name exactly as it appears on the receipt",
        examples=["PRLE G", "Rice", "Harpic BTL"],
    )
    quantity: Decimal = Field(
        ...,
        gt=0,
        description="Number of units purchased",
        examples=[25, 1.5, 12],
    )
    unit: str = Field(
        ...,
        min_length=1,
        description="Purchase unit as written on the receipt (kg, pkt, pcs, btl, etc.)",
        examples=["kg", "packet", "pcs", "bottle"],
    )
    unit_price: Decimal | None = Field(
        default=None,
        ge=0,
        description="Price per unit in local currency",
    )
    line_total: Decimal | None = Field(
        default=None,
        ge=0,
        description="Total for this line (quantity × unit_price)",
    )

    @field_validator("raw_name")
    @classmethod
    def strip_raw_name(cls, v: str) -> str:
        return v.strip()

    @field_validator("unit")
    @classmethod
    def normalise_unit_basic(cls, v: str) -> str:
        """Strip whitespace and lowercase the unit for consistency."""
        return v.strip().lower()


# ── Full receipt ───────────────────────────────────────────────────────────────

class ExtractedReceipt(BaseModel):
    """
    Structured representation of a receipt as returned by Gemini Vision.

    All fields except `items` are nullable because receipt images vary
    wildly — a handwritten chit may have no supplier name, no date, and
    no total, yet still be a valid receipt with valid purchase data.
    """

    supplier: str | None = Field(
        default=None,
        description="Supplier / wholesaler name as written on the receipt",
    )
    date: str | None = Field(
        default=None,
        description="Receipt date in ISO 8601 format (YYYY-MM-DD) if detectable",
        examples=["2026-09-29", None],
    )
    invoice_number: str | None = Field(
        default=None,
        description="Invoice or bill number if present",
    )
    items: list[ExtractedItem] = Field(
        default_factory=list,
        description="All line items on the receipt — may be an arbitrary number",
    )
    receipt_total: Decimal | None = Field(
        default=None,
        ge=0,
        description="Grand total printed on the receipt",
    )
    currency: str = Field(
        default="INR",
        description="Currency code detected or assumed",
    )
    extraction_confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Overall confidence in the extraction quality [0, 1]",
    )
    extraction_notes: str | None = Field(
        default=None,
        description="Any warnings or ambiguities noted during extraction",
    )

    @model_validator(mode="after")
    def check_has_items(self) -> "ExtractedReceipt":
        if not self.items:
            self.extraction_notes = (
                (self.extraction_notes or "") + " Warning: no line items extracted."
            ).strip()
        return self

    @property
    def item_count(self) -> int:
        return len(self.items)

    @property
    def computed_total(self) -> Decimal | None:
        """Sum of all line_totals — used for cross-validation in Phase 5."""
        totals = [item.line_total for item in self.items if item.line_total is not None]
        if not totals:
            return None
        return sum(totals, Decimal("0"))
