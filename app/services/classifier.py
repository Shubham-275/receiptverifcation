"""
Receipt Classifier — Phase 3.

Uses Gemini Vision to decide whether an uploaded image is a supplier/
wholesaler receipt before running the more expensive extraction step.

Design:
  - Single Gemini call with a structured JSON prompt.
  - Configurable confidence threshold (settings.receipt_confidence_threshold).
  - Returns a ClassificationResult dataclass; callers decide what to do.
  - Raises AIServiceError on Gemini failure so callers can handle gracefully.
  - Client is instantiated lazily and reused across requests.

Uses the new `google-genai` SDK (google.genai), NOT the deprecated
`google-generativeai` package.
"""
import json
import re
import time
from dataclasses import dataclass
from typing import Literal

from google import genai
from google.genai import types as genai_types

from app.config import get_settings
from app.exceptions import AIServiceError
from app.logging_config import get_logger

log = get_logger(__name__)

# ── Prompt ─────────────────────────────────────────────────────────────────────

_CLASSIFICATION_PROMPT = """
You are a document classifier for a retail purchasing system.

Your task: determine whether the image is a **supplier or wholesaler receipt/invoice** —
a document that a small retailer receives when buying goods from a supplier.

A SUPPLIER RECEIPT contains ALL of these:
  1. A list of purchased goods/products (at least one item).
  2. Quantities (numeric amounts with or without units).
  3. Prices (per item or totals, in any currency).
  4. May include: supplier name, date, invoice number, bill total.
  5. May be printed, handwritten, typed, or photographed.

Do NOT classify as a receipt if the image is:
  - An OTP / verification code message
  - A bank transaction SMS or notification
  - A calculator display or arithmetic working
  - A plain photograph (people, food, landscape, etc.)
  - A screenshot of an app or website
  - Blank or unreadable

Respond ONLY with valid JSON (no markdown, no extra text):
{
  "is_receipt": <true|false>,
  "confidence": <float 0.0 to 1.0>,
  "document_type": <"supplier_purchase_receipt" | "invoice" | "consumer_receipt" | "not_a_receipt" | "unclear">,
  "reason": "<one sentence explanation>"
}

Rules for confidence:
  - 0.95+ : Unambiguously a supplier receipt with clear items and prices.
  - 0.80-0.95: Likely a receipt but some details are unclear (e.g. handwritten, partial).
  - 0.60-0.80: Could be a receipt but significant uncertainty.
  - Below 0.60: Not a receipt or highly ambiguous.
""".strip()


# ── Result dataclass ───────────────────────────────────────────────────────────

DocumentType = Literal[
    "supplier_purchase_receipt",
    "invoice",
    "consumer_receipt",
    "not_a_receipt",
    "unclear",
]


@dataclass
class ClassificationResult:
    is_receipt: bool
    confidence: float
    document_type: DocumentType
    reason: str
    latency_ms: float
    accepted: bool          # True if confidence ≥ threshold
    threshold_used: float   # the threshold that was applied

    def to_dict(self) -> dict:
        return {
            "is_receipt": self.is_receipt,
            "confidence": self.confidence,
            "document_type": self.document_type,
            "reason": self.reason,
            "accepted": self.accepted,
            "latency_ms": round(self.latency_ms, 1),
        }


# ── Gemini client (module-level lazy singleton) ────────────────────────────────

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
        log.info("gemini_client_initialized", model=settings.gemini_vision_model)
    return _client


# ── JSON extraction helper ─────────────────────────────────────────────────────

def _parse_classification_response(raw_text: str) -> dict:
    """
    Extract JSON from the model response.
    Handles cases where the model wraps JSON in markdown code fences.
    """
    text = raw_text.strip()

    # Strip markdown fences if present
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if fenced:
        text = fenced.group(1).strip()

    # Find the first {...} block
    brace_match = re.search(r"\{[\s\S]*\}", text)
    if brace_match:
        text = brace_match.group(0)

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIServiceError(
            "Gemini returned unparseable classification response.",
            detail=f"Raw response: {raw_text[:300]}  Error: {exc}",
        )


def _validate_parsed_response(data: dict) -> tuple[bool, float, DocumentType, str]:
    """Validate and coerce parsed JSON fields."""
    valid_types: set[DocumentType] = {
        "supplier_purchase_receipt", "invoice",
        "consumer_receipt", "not_a_receipt", "unclear",
    }

    is_receipt = bool(data.get("is_receipt", False))
    confidence = float(data.get("confidence", 0.0))
    confidence = max(0.0, min(1.0, confidence))  # clamp to [0, 1]

    doc_type = data.get("document_type", "unclear")
    if doc_type not in valid_types:
        doc_type = "unclear"

    reason = str(data.get("reason", "No reason provided."))[:500]

    return is_receipt, confidence, doc_type, reason  # type: ignore[return-value]


# ── Public API ─────────────────────────────────────────────────────────────────

async def classify_receipt_image(image_bytes: bytes, mime_type: str) -> ClassificationResult:
    """
    Classify whether image_bytes represents a supplier receipt.

    Args:
        image_bytes: Raw bytes of the image (already validated by image_validator).
        mime_type:   MIME type string, e.g. "image/jpeg".

    Returns:
        ClassificationResult with is_receipt, confidence, document_type, reason.

    Raises:
        AIServiceError: If Gemini is unavailable or returns an unparseable response.
    """
    settings = get_settings()
    log.info("classifying_image", size_bytes=len(image_bytes), mime=mime_type, provider=settings.get_effective_provider())
    t0 = time.perf_counter()

    if settings.get_effective_provider() == "openrouter":
        from app.services.openrouter_client import generate_chat_completion

        raw_text = await generate_chat_completion(
            prompt=_CLASSIFICATION_PROMPT,
            image_bytes=image_bytes,
            mime_type=mime_type,
            temperature=0.1,
            max_tokens=256,
        )
    else:
        client = _get_client()
        image_part = genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

        try:
            response = client.models.generate_content(
                model=settings.gemini_vision_model,
                contents=[image_part, _CLASSIFICATION_PROMPT],
                config=genai_types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=256,
                    # Keep the JSON answer inside the small token budget.
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
                "Gemini Vision call failed during receipt classification.",
                detail=detail,
            )

        raw_text = response.text if response.text else ""

    latency_ms = (time.perf_counter() - t0) * 1000

    if not raw_text:
        raise AIServiceError(
            "AI model returned an empty response during classification.",
        )

    log.debug("classification_raw_response", raw=raw_text[:400], latency_ms=round(latency_ms, 1))

    parsed = _parse_classification_response(raw_text)
    is_receipt, confidence, doc_type, reason = _validate_parsed_response(parsed)

    threshold = settings.receipt_confidence_threshold
    accepted = is_receipt and confidence >= threshold

    result = ClassificationResult(
        is_receipt=is_receipt,
        confidence=confidence,
        document_type=doc_type,
        reason=reason,
        latency_ms=latency_ms,
        accepted=accepted,
        threshold_used=threshold,
    )

    log.info(
        "classification_complete",
        is_receipt=is_receipt,
        confidence=round(confidence, 3),
        document_type=doc_type,
        accepted=accepted,
        latency_ms=round(latency_ms, 1),
    )

    return result
