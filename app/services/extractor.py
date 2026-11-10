"""
Receipt Extractor — Phase 4.

Uses Gemini Vision to extract structured purchase data from a receipt image
that has already passed the classifier gate.

Design:
  - One Gemini call with a detailed extraction prompt.
  - Returns an ExtractedReceipt Pydantic model.
  - Supports arbitrary number of line items — no hardcoded limit.
  - Missing fields (date, supplier, total) handled gracefully.
  - Robust JSON parsing with markdown-fence stripping + repair heuristics.
  - Lazy genai.Client singleton shared with classifier.

Uses the google-genai SDK (google.genai ≥ 2.x).
"""
import json
import re
import time
from typing import Any

from google import genai
from google.genai import types as genai_types

from app.config import get_settings
from app.exceptions import AIServiceError, ExtractionError
from app.logging_config import get_logger
from app.schemas.extraction import ExtractedItem, ExtractedReceipt

log = get_logger(__name__)

# ── Extraction prompt ──────────────────────────────────────────────────────────

_EXTRACTION_PROMPT = """
You are a receipt data extractor for a retail purchasing system.

Extract ALL information from this supplier/wholesaler receipt image.

OUTPUT FORMAT — respond with ONLY valid JSON, no markdown fences, no extra text:

{
  "supplier": "<supplier name or null>",
  "date": "<ISO date YYYY-MM-DD or null if not found>",
  "invoice_number": "<invoice/bill number or null>",
  "receipt_total": <total amount as number, or null>,
  "currency": "<currency code, default INR>",
  "extraction_confidence": <your overall confidence 0.0 to 1.0>,
  "extraction_notes": "<brief note about any ambiguities, or null>",
  "items": [
    {
      "raw_name": "<product name exactly as on receipt>",
      "quantity": <numeric quantity>,
      "unit": "<unit: kg, g, litre, ml, piece, pcs, packet, pkt, box, bottle, btl, dozen, etc.>",
      "unit_price": <price per unit as number, or null>,
      "line_total": <line total as number, or null>
    }
  ]
}

STRICT RULES:
1. Extract EVERY line item — do not skip any product, even if the name is unclear.
2. Preserve the raw product name EXACTLY as written — do not correct spelling.
3. If a field is missing or unreadable, use null — do not guess.
4. Quantities must be positive numbers. Units must be strings.
5. Prices must be non-negative numbers. Do not include currency symbols.
6. If the date is in DD/MM/YYYY or DD-MM-YYYY format, convert to YYYY-MM-DD.
7. This system serves retailers in India. When a numeric date is ambiguous (for example 02/11/2026), read it as DD/MM/YYYY rather than MM/DD/YYYY.
7. If only a line total is visible (no unit price), set unit_price to null.
8. If only a unit price is visible (no line total), compute line_total = quantity × unit_price.
9. Do not add items that are not on the receipt.
9a. NEVER output summary/footer lines as items. Ignore: "TOT QTY", "Total Qty", "Sub Total",
    "Total", "GST", "Tax", "Discount", "Rounding", "Cash", "Card", "Change", "Balance",
    "GST Summary" tables, and any payment lines. Only purchased products are items.
9b. Read each item row using the table's column headers (Qty, Price/RSP, Amount).
    Quantity is the number in the Qty column, NOT a handwritten annotation or item code.
    Check: quantity × unit_price should equal line_total; if not, re-read the row.
9c. Some receipts print an item code (e.g. 6783) on one line and the product description on
    the next line (often prefixed "SR:"). Combine them into ONE item whose raw_name is the
    description (without the "SR:" tax-code prefix and without the code), with the numbers
    from the code line. Do not create a separate item for the description line.
9d. Only use a unit if it is PRINTED next to the quantity (kg, pcs, box, btl, etc.).
    Handwritten marks, stamps, initials or codes beside a quantity (e.g. "GA", "GS", "1809")
    are NOT units — ignore them. If no printed unit is shown, use "pcs".
10. Set extraction_confidence based on image clarity and completeness:
    - 0.95+: clear, all fields readable
    - 0.75-0.95: mostly readable, minor ambiguities
    - 0.50-0.75: significant blur/handwriting issues
    - Below 0.50: very poor quality

EXAMPLE OUTPUT for a receipt with 3 items:
{
  "supplier": "Sharma Traders",
  "date": "2026-09-29",
  "invoice_number": "ST-4821",
  "receipt_total": 3270,
  "currency": "INR",
  "extraction_confidence": 0.95,
  "extraction_notes": null,
  "items": [
    {"raw_name": "Rice", "quantity": 25, "unit": "kg", "unit_price": 74, "line_total": 1850},
    {"raw_name": "PRLE G", "quantity": 20, "unit": "pkt", "unit_price": 10, "line_total": 200},
    {"raw_name": "Harpic", "quantity": 6, "unit": "btl", "unit_price": 90, "line_total": 540}
  ]
}
""".strip()


# ── Gemini client singleton ────────────────────────────────────────────────────

_client: genai.Client | None = None
_cached_gemini_key: str | None = None


def _get_client() -> genai.Client:
    global _client, _cached_gemini_key
    settings = get_settings()
    if not settings.gemini_api_key:
        raise AIServiceError(
            "Gemini API key not configured.",
            detail="Set GEMINI_API_KEY in your .env file.",
        )
    if _client is None or _cached_gemini_key != settings.gemini_api_key:
        _client = genai.Client(api_key=settings.gemini_api_key)
        _cached_gemini_key = settings.gemini_api_key
        log.info("gemini_extractor_client_initialized", model=settings.gemini_vision_model)
    return _client


# ── JSON parsing helpers ───────────────────────────────────────────────────────

def _extract_json_from_text(raw: str) -> dict[str, Any]:
    """
    Pull JSON out of a model response that may contain:
      - Markdown code fences (```json ... ```)
      - Preamble / postamble text
      - Trailing commas (light repair)
    """
    text = raw.strip()

    # Strip markdown fences
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if fenced:
        text = fenced.group(1).strip()

    # Find outermost {...}
    brace_match = re.search(r"\{[\s\S]*\}", text)
    if brace_match:
        text = brace_match.group(0)

    # Light repair: remove trailing commas before } or ]
    text = re.sub(r",\s*([\}\]])", r"\1", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExtractionError(
            "Gemini returned unparseable extraction JSON.",
            detail=f"Parse error: {exc}. Raw (truncated): {raw[:500]}",
        )


def _coerce_decimal_field(value: Any, field: str) -> str | None:
    """
    Coerce various numeric representations to a string Decimal can parse.
    Handles: int, float, str "1,850" (Indian comma notation), None.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        # Remove commas (e.g. "1,850" → "1850")
        cleaned = value.replace(",", "").strip()
        if cleaned in ("", "null", "None", "N/A", "-"):
            return None
        return cleaned
    log.warning("unexpected_field_type", field=field, type=type(value).__name__, value=value)
    return None


def _parse_items(raw_items: list[Any]) -> list[ExtractedItem]:
    """Parse and validate each line item, skipping unparseable ones with a warning."""
    items: list[ExtractedItem] = []
    for idx, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            log.warning("skipping_non_dict_item", index=idx, raw=raw)
            continue
        try:
            item = ExtractedItem(
                raw_name=str(raw.get("raw_name", "")).strip() or f"Item {idx + 1}",
                quantity=_coerce_decimal_field(raw.get("quantity"), "quantity") or "1",
                unit=str(raw.get("unit", "piece")).strip() or "piece",
                unit_price=_coerce_decimal_field(raw.get("unit_price"), "unit_price"),
                line_total=_coerce_decimal_field(raw.get("line_total"), "line_total"),
            )
            items.append(item)
        except Exception as exc:
            log.warning("skipping_unparseable_item", index=idx, raw=raw, error=str(exc))
    return items


def _build_extracted_receipt(data: dict[str, Any]) -> ExtractedReceipt:
    """Convert the raw parsed dict into a validated ExtractedReceipt."""
    raw_items = data.get("items", [])
    if not isinstance(raw_items, list):
        raw_items = []

    items = _parse_items(raw_items)

    confidence_raw = data.get("extraction_confidence", 1.0)
    try:
        confidence = float(confidence_raw)
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.5

    return ExtractedReceipt(
        supplier=str(data["supplier"]).strip() if data.get("supplier") else None,
        date=str(data["date"]).strip() if data.get("date") else None,
        invoice_number=str(data["invoice_number"]).strip() if data.get("invoice_number") else None,
        items=items,
        receipt_total=_coerce_decimal_field(data.get("receipt_total"), "receipt_total"),
        currency=str(data.get("currency", "INR")).strip().upper() or "INR",
        extraction_confidence=confidence,
        extraction_notes=str(data["extraction_notes"]).strip() if data.get("extraction_notes") else None,
    )


# ── Public API ─────────────────────────────────────────────────────────────────

async def extract_receipt(image_bytes: bytes, mime_type: str) -> ExtractedReceipt:
    """
    Extract structured purchase data from a receipt image.

    Args:
        image_bytes: Raw image bytes (already classified as a receipt).
        mime_type:   MIME type, e.g. "image/jpeg".

    Returns:
        ExtractedReceipt with supplier, date, invoice, and all line items.

    Raises:
        AIServiceError:   If Gemini call fails.
        ExtractionError:  If the response cannot be parsed into a valid receipt.
    """
    settings = get_settings()
    log.info("extracting_receipt", size_bytes=len(image_bytes), mime=mime_type, provider=settings.get_effective_provider())
    t0 = time.perf_counter()

    if settings.get_effective_provider() == "openrouter":
        from app.services.openrouter_client import generate_chat_completion

        raw_text = await generate_chat_completion(
            prompt=_EXTRACTION_PROMPT,
            image_bytes=image_bytes,
            mime_type=mime_type,
            temperature=0.1,
            max_tokens=4096,
        )
    else:
        client = _get_client()
        image_part = genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

        try:
            response = client.models.generate_content(
                model=settings.gemini_vision_model,
                contents=[image_part, _EXTRACTION_PROMPT],
                config=genai_types.GenerateContentConfig(
                    temperature=0.1,        # low temperature for factual extraction
                    max_output_tokens=4096, # receipts can have many items
                    thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
                ),
            )
        except Exception as exc:
            err_str = str(exc)
            detail = err_str
            if "not allowed by policy" in err_str or "Forbidden" in err_str:
                detail = (
                    "Google rejected the request (403 Policy Forbidden): The API key policy does not allow access "
                    "to 'generativelanguage.googleapis.com'. Please use a free API key generated from Google AI Studio "
                    "(https://aistudio.google.com/app/apikey) or enable 'Generative Language API' in Google Cloud Console > Credentials."
                )
            raise AIServiceError(
                "Gemini Vision call failed during receipt extraction.",
                detail=detail,
            )

        raw_text = response.text if response.text else ""

    latency_ms = (time.perf_counter() - t0) * 1000

    if not raw_text:
        raise ExtractionError(
            "AI model returned an empty response during extraction.",
        )

    log.debug(
        "extraction_raw_response",
        length=len(raw_text),
        preview=raw_text[:200],
        latency_ms=round(latency_ms, 1),
    )

    parsed = _extract_json_from_text(raw_text)
    receipt = _build_extracted_receipt(parsed)

    log.info(
        "extraction_complete",
        supplier=receipt.supplier,
        date=receipt.date,
        item_count=receipt.item_count,
        confidence=round(receipt.extraction_confidence, 3),
        latency_ms=round(latency_ms, 1),
    )

    return receipt
