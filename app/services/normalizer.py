"""
Receipt Validation and Normalization — Phase 5.

Takes an ExtractedReceipt (raw Gemini output) and:

1. VALIDATES integrity constraints:
   - quantity × unit_price ≈ line_total (per item)
   - sum(line_totals) ≈ receipt_total
   - Accounts for discounts, taxes, rounding, and missing totals

2. NORMALIZES fields:
   - Date strings → Python date objects
   - Unit abbreviations → canonical forms (KG → kg, PKT → packet, etc.)
   - Supplier name → stripped/titlecased
   - Missing line_total computed from quantity × unit_price
   - Missing unit_price computed from line_total ÷ quantity

3. Returns a ValidationResult containing:
   - The (potentially corrected) ExtractedReceipt
   - Per-item validation results
   - A summary of discrepancies
   - An overall is_valid flag

Design:
  - Does NOT reject a receipt solely because totals don't match.
  - Records discrepancies for traceability.
  - Never silently discards line items.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import NamedTuple

from app.logging_config import get_logger
from app.schemas.extraction import ExtractedItem, ExtractedReceipt

log = get_logger(__name__)

# ── Tolerance for math checks ──────────────────────────────────────────────────
# Allow 2% or ₹2 rounding error on individual lines
_ITEM_TOLERANCE_RATIO = Decimal("0.02")
_ITEM_TOLERANCE_ABS = Decimal("2.00")
# Allow 5% or ₹10 error on the receipt total (discounts, taxes)
_TOTAL_TOLERANCE_RATIO = Decimal("0.05")
_TOTAL_TOLERANCE_ABS = Decimal("10.00")

# ── Unit normalization map ─────────────────────────────────────────────────────
# Maps common abbreviations and variants → canonical unit strings.
# This is deliberately NOT exhaustive — unknown units pass through unchanged.
_UNIT_MAP: dict[str, str] = {
    # weight
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg",
    "g": "g", "gm": "g", "gms": "g", "gram": "g", "grams": "g",
    # volume
    "l": "litre", "lt": "litre", "ltr": "litre", "litre": "litre",
    "litres": "litre", "liter": "litre", "liters": "litre",
    "ml": "ml", "mls": "ml",
    # pieces / units
    "pc": "piece", "pcs": "piece", "piece": "piece", "pieces": "piece",
    "nos": "piece", "no": "piece", "unit": "piece", "units": "piece",
    "ea": "piece", "each": "piece",
    # packets
    "pkt": "packet", "pkts": "packet", "packet": "packet", "packets": "packet",
    "pack": "packet", "packs": "packet",
    # bottles
    "btl": "bottle", "btls": "bottle", "bottle": "bottle", "bottles": "bottle",
    "bot": "bottle", "bots": "bottle",
    # boxes / cartons
    "box": "box", "boxes": "box", "bx": "box",
    "ctn": "carton", "carton": "carton", "cartons": "carton",
    # dozen
    "doz": "dozen", "dozen": "dozen", "dz": "dozen",
    # strips (medicines, biscuits)
    "strip": "strip", "strips": "strip",
    # rolls
    "roll": "roll", "rolls": "roll",
    # pouches
    "pouch": "pouch", "pouches": "pouch",
    # bags
    "bag": "bag", "bags": "bag",
}


def _normalise_unit(raw_unit: str) -> str:
    """Map raw unit string to a canonical form using the unit map."""
    key = raw_unit.strip().lower()
    return _UNIT_MAP.get(key, key)


def _parse_date(raw: str | None) -> date | None:
    """
    Try to parse a date string into a Python date object.
    Accepts:
      - YYYY-MM-DD (ISO)
      - DD/MM/YYYY
      - DD-MM-YYYY
      - DD.MM.YYYY
    Returns None on failure.
    """
    if not raw:
        return None

    # Already ISO
    iso_match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw.strip())
    if iso_match:
        try:
            return date(int(iso_match[1]), int(iso_match[2]), int(iso_match[3]))
        except ValueError:
            pass

    # DD/MM/YYYY or DD-MM-YYYY or DD.MM.YYYY
    dmy_match = re.fullmatch(r"(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{4})", raw.strip())
    if dmy_match:
        try:
            return date(int(dmy_match[3]), int(dmy_match[2]), int(dmy_match[1]))
        except ValueError:
            pass

    log.warning("date_parse_failed", raw=raw)
    return None


# ── Per-item validation ────────────────────────────────────────────────────────

class ItemValidation(NamedTuple):
    index: int
    raw_name: str
    math_ok: bool
    math_discrepancy: Decimal | None   # |computed - stated| if mismatch
    line_total_filled: bool             # True if we computed a missing line_total
    unit_price_filled: bool             # True if we computed a missing unit_price
    notes: str


def _validate_and_complete_item(
    item: ExtractedItem,
    index: int,
) -> tuple[ExtractedItem, ItemValidation]:
    """
    Validate and complete a single line item.
    Returns (potentially modified item, validation result).
    """
    qty = item.quantity
    up = item.unit_price
    lt = item.line_total
    notes_parts: list[str] = []
    line_total_filled = False
    unit_price_filled = False
    math_ok = True
    discrepancy: Decimal | None = None

    # Compute missing fields
    if lt is None and up is not None:
        lt = (qty * up).quantize(Decimal("0.01"))
        line_total_filled = True
        notes_parts.append("line_total computed from qty × unit_price")

    if up is None and lt is not None and qty > 0:
        try:
            up = (lt / qty).quantize(Decimal("0.01"))
            unit_price_filled = True
            notes_parts.append("unit_price computed from line_total ÷ qty")
        except InvalidOperation:
            pass

    # Validate math: qty × unit_price ≈ line_total
    if up is not None and lt is not None:
        expected = (qty * up).quantize(Decimal("0.01"))
        diff = abs(expected - lt)
        tolerance = max(
            lt * _ITEM_TOLERANCE_RATIO,
            _ITEM_TOLERANCE_ABS,
        )
        if diff > tolerance:
            math_ok = False
            discrepancy = diff
            notes_parts.append(
                f"math mismatch: {qty} × {up} = {expected}, stated total = {lt} (diff={diff})"
            )

    # Update item with filled fields
    updated = item.model_copy(update={"unit_price": up, "line_total": lt})

    validation = ItemValidation(
        index=index,
        raw_name=item.raw_name,
        math_ok=math_ok,
        math_discrepancy=discrepancy,
        line_total_filled=line_total_filled,
        unit_price_filled=unit_price_filled,
        notes=" | ".join(notes_parts) if notes_parts else "ok",
    )

    return updated, validation


# ── Summary-row filtering ──────────────────────────────────────────────────────

# Rows that belong to the receipt footer/summary, never to the product list.
_SUMMARY_ROW_RE = re.compile(
    r"^\W*("
    r"tot(al)?\.?\s*(qty|quantity|items?|amount|amt|due)?"
    r"|tot(al)?\.?\s*(incl\w*|excl\w*)?\.?\s*(of\s*)?(gst|tax|sst|vat)"
    r"|grand\s*total|sub\s*-?\s*total|net\s*(total|amount|payable)"
    r"|(excluded|including|inclusive|exclusive|excl|incl)\.?\s*(of\s*)?(gst|tax|vat)?.*"
    r"|gst(\s*(@|\d|summary|total|amount|payable|registered)?.*)?"
    r"|(sst|vat|tax)(\s*(@|\d|total|summary).*)?"
    r"|discount.*|round(ing|ed)?(\s*(adj|adjustment|amt|amount|off))?"
    r"|(visa|master|debit|credit|amex)\s*(card)?|cash|card|change|balance|amount\s*(paid|tendered|due)"
    r"|tender(ed)?|payment|paid|qty\s*:?\s*\d*"
    r")\W*(\(?(rm|inr|rs|usd|myr)\)?)?\W*[\d.,:\s]*$",
    re.IGNORECASE,
)


def _is_summary_row(item: ExtractedItem) -> bool:
    """True if the 'item' is really a totals/summary line, not a purchased product."""
    name = (item.raw_name or "").strip()
    if not name:
        return True
    return bool(_SUMMARY_ROW_RE.match(name))


def _drop_summary_rows(items: list[ExtractedItem]) -> list[ExtractedItem]:
    kept = [it for it in items if not _is_summary_row(it)]
    dropped = [it.raw_name for it in items if _is_summary_row(it)]
    if dropped:
        log.info("summary_rows_dropped", dropped=dropped)
    return kept


# ── Receipt-level validation ───────────────────────────────────────────────────

@dataclass
class ValidationResult:
    receipt: ExtractedReceipt           # possibly updated with computed fields
    receipt_date: date | None           # parsed date
    item_validations: list[ItemValidation]
    total_match: bool                   # sum(line_totals) ≈ receipt_total
    total_discrepancy: Decimal | None   # |computed_sum - stated_total|
    item_math_failures: int             # number of items with math mismatches
    is_valid: bool                      # overall verdict
    summary: str                        # human-readable summary


def validate_and_normalise(raw: ExtractedReceipt) -> ValidationResult:
    """
    Validate and normalise an ExtractedReceipt.

    Args:
        raw: The ExtractedReceipt directly from the extractor.

    Returns:
        ValidationResult with corrected data and full audit trail.
    """
    log.info(
        "validating_receipt",
        supplier=raw.supplier,
        item_count=raw.item_count,
        stated_total=str(raw.receipt_total),
    )

    # 0. Remove footer/summary rows mistakenly extracted as products
    #    (e.g. "TOT QTY: 48", "Sub Total", "GST @6%", "Rounding")
    filtered_items = _drop_summary_rows(list(raw.items))

    # 1. Normalise unit strings
    normalised_items = [
        item.model_copy(update={"unit": _normalise_unit(item.unit)})
        for item in filtered_items
    ]

    # 2. Normalise supplier name
    supplier = raw.supplier.strip().title() if raw.supplier else None

    # 3. Parse date
    receipt_date = _parse_date(raw.date)

    # 4. Validate and complete each item
    completed_items: list[ExtractedItem] = []
    item_validations: list[ItemValidation] = []
    for idx, item in enumerate(normalised_items):
        completed, validation = _validate_and_complete_item(item, idx)
        completed_items.append(completed)
        item_validations.append(validation)
        if not validation.math_ok:
            log.warning(
                "item_math_mismatch",
                index=idx,
                raw_name=item.raw_name,
                notes=validation.notes,
            )

    # 5. Cross-check receipt total
    computed_sum = sum(
        (item.line_total for item in completed_items if item.line_total is not None),
        Decimal("0"),
    )
    stated_total = raw.receipt_total
    total_match = True
    total_discrepancy: Decimal | None = None

    if stated_total is not None and computed_sum > 0:
        diff = abs(computed_sum - stated_total)
        tolerance = max(
            stated_total * _TOTAL_TOLERANCE_RATIO,
            _TOTAL_TOLERANCE_ABS,
        )
        if diff > tolerance:
            total_match = False
            total_discrepancy = diff
            log.warning(
                "receipt_total_mismatch",
                stated=str(stated_total),
                computed=str(computed_sum),
                diff=str(diff),
            )

    # 6. Build corrected receipt
    corrected = raw.model_copy(
        update={
            "supplier": supplier,
            "items": completed_items,
        }
    )

    # 7. Compute overall validity
    item_math_failures = sum(1 for v in item_validations if not v.math_ok)
    is_valid = (
        len(completed_items) > 0
        # Not a hard failure — just flag it
    )

    # 8. Summary
    parts = [
        f"{len(completed_items)} item(s) extracted",
        f"confidence={corrected.extraction_confidence:.0%}",
    ]
    if not total_match and total_discrepancy is not None:
        parts.append(f"total mismatch ₹{total_discrepancy:.2f}")
    if item_math_failures:
        parts.append(f"{item_math_failures} item(s) with math issues")
    if receipt_date:
        parts.append(f"date={receipt_date.isoformat()}")

    summary = "; ".join(parts)

    log.info(
        "validation_complete",
        is_valid=is_valid,
        item_count=len(completed_items),
        total_match=total_match,
        item_math_failures=item_math_failures,
        summary=summary,
    )

    return ValidationResult(
        receipt=corrected,
        receipt_date=receipt_date,
        item_validations=item_validations,
        total_match=total_match,
        total_discrepancy=total_discrepancy,
        item_math_failures=item_math_failures,
        is_valid=is_valid,
        summary=summary,
    )
