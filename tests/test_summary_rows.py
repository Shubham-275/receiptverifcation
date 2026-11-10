"""Summary/footer rows (TOT QTY, GST, Rounding...) must never become line items."""
import pytest

from app.schemas.extraction import ExtractedItem, ExtractedReceipt
from app.services.normalizer import _is_summary_row, validate_and_normalise


def _item(name, qty=1, price=1, total=1):
    return ExtractedItem(
        raw_name=name, quantity=qty, unit="pcs", unit_price=price, line_total=total
    )


@pytest.mark.parametrize(
    "name",
    [
        "TOT QTY",
        "TOT QTY:",
        "TOT QTY: 48",
        "Total Qty",
        "Total",
        "Sub Total (RM)",
        "Sub Total",
        "Grand Total",
        "GST @6%",
        "Total GST (RM)",
        "Discount (RM)",
        "Rounding (RM)",
        "Cash",
        "Change (RM)",
        "VISA CARD",
        "(Excluded GST) Sub Total (RM)",
        "Total Inclusive GST",
        "Total Exclude GST",
        "Qty: 1",
    ],
)
def test_summary_rows_are_detected(name):
    assert _is_summary_row(_item(name)), name


@pytest.mark.parametrize(
    "name",
    [
        "CERAMIC CAP",
        "S/STEEL 1/2\" STREET ELBOW",
        "WELDRO PICKLING GEL 1KG",
        "13.5\" WELDING GLOVE - GREEN (GS)",
        "Rice Super Fine",
        "Total Gold Soap",
        "Tax Invoice Paper Roll",
        "Cash Box",
        "Card Holder",
        "KINGS SAFETY SHOES KWD 805",
        "Change Purse",
    ],
)
def test_real_products_are_kept(name):
    assert not _is_summary_row(_item(name)), name


def test_pipeline_drops_tot_qty_row():
    receipt = ExtractedReceipt(
        supplier="Perniagaan Zheng Hui",
        date="2018-02-09",
        receipt_total=411.50,
        extraction_confidence=0.9,
        items=[
            _item("CERAMIC CAP", 5, 3.5, 17.5),
            _item("S/STEEL STREET ELBOW", 30, 3.8, 114),
            _item("TOT QTY", 48, 8.57, 411.5),
        ],
    )
    result = validate_and_normalise(receipt)
    names = [i.raw_name for i in result.receipt.items]
    assert "TOT QTY" not in names
    assert len(names) == 2
