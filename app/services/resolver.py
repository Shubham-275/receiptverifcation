"""
Product Entity Resolver — Phase 6.

Resolves a raw product name (as extracted from a receipt) to a canonical
Product record using a 5-step cascade, ordered from cheapest to most expensive:

  Step 1: Exact alias lookup      (DB index scan — O(1))
  Step 2: Normalized name match   (DB index scan — O(1))
  Step 3: Fuzzy matching          (RapidFuzz in-memory — O(N×M))
  Step 4: pgvector similarity     (ANN index — O(log N))
  Step 5: LLM contextual reasoning (Gemini — expensive, used sparingly)

After resolution, the result is recorded in alias_observations. If
confidence is high enough and repetition supports it, a new alias is
auto-accepted without human intervention.

Design principles:
  - Do not call step N+1 if step N succeeded.
  - Supplier context always checked first for supplier-scoped aliases.
  - LLM is only invoked if earlier steps returned candidates but were ambiguous.
  - Unresolved items are stored with resolution_method="unresolved" for
    deferred resolution when more context is available.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional

from rapidfuzz import fuzz, process
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.exceptions import AIServiceError
from app.logging_config import get_logger
from app.models.product import Product
from app.models.product_alias import AliasSource, ProductAlias
from app.models.purchase_item import ResolutionMethod
from app.repositories import product_repo
from app.services.embedding import embed_text  # imported at module level → patchable

log = get_logger(__name__)

# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass
class ResolutionResult:
    """
    The outcome of attempting to resolve a raw product name.

    If product is None, the item is unresolved and needs_confirmation=True
    unless it is simply a new product with no candidates.
    """
    raw_name: str
    product: Optional[Product]              # None if unresolved
    confidence: float                       # [0, 1]
    method: ResolutionMethod
    alias_id: Optional[uuid.UUID] = None   # set if resolved via exact alias
    candidates: list[Product] = field(default_factory=list)  # alternatives
    needs_confirmation: bool = False
    notes: str = ""


# ── LLM-based contextual reasoning (Step 5) ───────────────────────────────────

_DISAMBIGUATION_PROMPT_TEMPLATE = """
You are resolving a product name from a supplier receipt to a canonical product.

Raw name from receipt: "{raw_name}"
Supplier context: {supplier_context}
Unit on receipt: {unit_context}

Candidate canonical products:
{candidates}

Which candidate best matches the raw name? Consider:
- String similarity
- Common abbreviations in Indian wholesale trade
- The supplier and unit as context clues
- If none match, say "none"

Respond with ONLY valid JSON:
{{
  "best_match_name": "<canonical name or null>",
  "confidence": <float 0.0 to 1.0>,
  "reason": "<one sentence>"
}}
"""


async def _llm_disambiguate(
    raw_name: str,
    candidates: list[Product],
    supplier_name: str | None,
    unit: str | None,
) -> tuple[Product | None, float, str]:
    """
    Ask Gemini to pick the best candidate from a short list.
    Returns (product_or_none, confidence, reason).
    """
    import json
    import re

    from google import genai
    from google.genai import types as genai_types

    settings = get_settings()

    candidates_text = "\n".join(
        f"  - {c.canonical_name} (category: {c.category or 'unknown'})"
        for c in candidates
    )
    prompt = _DISAMBIGUATION_PROMPT_TEMPLATE.format(
        raw_name=raw_name,
        supplier_context=supplier_name or "unknown",
        unit_context=unit or "unknown",
        candidates=candidates_text,
    )

    if settings.get_effective_provider() == "openrouter":
        if not settings.openrouter_api_key:
            return None, 0.0, "OpenRouter key not configured"
        try:
            from app.services.openrouter_client import generate_chat_completion

            raw = await generate_chat_completion(
                prompt=prompt,
                temperature=0.1,
                max_tokens=128,
            )
        except Exception as exc:
            log.warning("llm_disambiguation_failed", error=str(exc))
            return None, 0.0, f"LLM error: {exc}"
    else:
        if not settings.gemini_api_key:
            return None, 0.0, "Gemini key not configured"

        try:
            client = genai.Client(api_key=settings.gemini_api_key)
            response = client.models.generate_content(
                model=settings.gemini_vision_model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=128,
                    thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
                ),
            )
            raw = response.text or ""
        except Exception as exc:
            log.warning("llm_disambiguation_failed", error=str(exc))
            return None, 0.0, f"LLM error: {exc}"

    # Parse response
    fenced = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
    text = fenced.group(1).strip() if fenced else raw.strip()
    brace = re.search(r"\{[\s\S]*\}", text)
    if brace:
        text = brace.group(0)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None, 0.0, "Unparseable LLM response"

    best_name = data.get("best_match_name")
    confidence = float(data.get("confidence", 0.0))
    confidence = max(0.0, min(1.0, confidence))
    reason = str(data.get("reason", ""))

    if not best_name or best_name.lower() == "none":
        return None, confidence, reason

    # Match best_name back to a candidate Product
    for c in candidates:
        if c.canonical_name.lower() == best_name.lower():
            return c, confidence, reason

    # Try partial match
    for c in candidates:
        if best_name.lower() in c.canonical_name.lower():
            return c, confidence * 0.9, reason + " (partial name match)"

    return None, 0.0, f"LLM returned '{best_name}' but no candidate matched"


# ── Self-learning: alias promotion ────────────────────────────────────────────

_MIN_OBSERVATIONS_FOR_AUTO_ACCEPT = 3  # must be seen at least N times

async def _maybe_promote_alias(
    session: AsyncSession,
    raw_name: str,
    product: Product,
    supplier_id: uuid.UUID | None,
    confidence: float,
    unit: str | None,
) -> bool:
    """
    Check whether this raw_name → product mapping should be promoted to a
    confirmed alias (auto-accepted into product_aliases).

    Rules:
      - Confidence must exceed settings.alias_auto_accept_confidence
      - Must have been seen >= _MIN_OBSERVATIONS_FOR_AUTO_ACCEPT times
      - Must not already exist as a confirmed alias

    Returns True if a new alias was created.
    """
    settings = get_settings()

    if confidence < settings.alias_auto_accept_confidence:
        return False

    # Check existing alias
    norm = product_repo.normalise_product_name(raw_name)
    existing = await product_repo.find_alias_exact(session, norm, supplier_id)
    if existing and existing.is_confirmed:
        return False  # already confirmed

    # Check observation count
    obs = await product_repo.get_alias_observation(session, norm, supplier_id)
    if obs is None or obs.occurrence_count < _MIN_OBSERVATIONS_FOR_AUTO_ACCEPT:
        return False

    # Promote — create confirmed alias
    await product_repo.create_alias(
        session=session,
        product_id=product.id,
        alias=raw_name,
        supplier_id=supplier_id,
        confidence=confidence,
        source=AliasSource.auto_learned,
        is_confirmed=True,
    )
    log.info(
        "alias_auto_promoted",
        raw_name=raw_name,
        canonical=product.canonical_name,
        confidence=round(confidence, 3),
        observations=obs.occurrence_count,
    )
    return True


# ── Main resolver ─────────────────────────────────────────────────────────────

class ProductResolver:
    """
    Resolves raw product names to canonical Product records.

    Instantiate per-request with an active AsyncSession.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self._settings = get_settings()

    async def resolve(
        self,
        raw_name: str,
        supplier_id: uuid.UUID | None = None,
        supplier_name: str | None = None,
        unit: str | None = None,
    ) -> ResolutionResult:
        """
        Run the resolution cascade for a single raw product name.

        Args:
            raw_name:      Raw name as extracted from the receipt.
            supplier_id:   UUID of the matched Supplier (for context).
            supplier_name: Human-readable supplier name (for LLM prompt).
            unit:          Purchase unit (additional context signal).

        Returns:
            ResolutionResult with product, confidence, and method.
        """
        norm = product_repo.normalise_product_name(raw_name)
        log.debug("resolving_product", raw_name=raw_name, normalised=norm)

        result = await self._run_cascade(raw_name, norm, supplier_id, supplier_name, unit)

        # Record the observation (for self-learning)
        await product_repo.upsert_alias_observation(
            session=self.session,
            raw_name=raw_name,
            supplier_id=supplier_id,
            resolved_product_id=result.product.id if result.product else None,
            resolution_confidence=result.confidence,
            unit=unit,
        )

        # Possibly auto-promote to confirmed alias
        if result.product and result.confidence >= self._settings.alias_auto_accept_confidence:
            await _maybe_promote_alias(
                session=self.session,
                raw_name=raw_name,
                product=result.product,
                supplier_id=supplier_id,
                confidence=result.confidence,
                unit=unit,
            )

        log.info(
            "product_resolved",
            raw_name=raw_name,
            canonical=result.product.canonical_name if result.product else None,
            method=result.method.value,
            confidence=round(result.confidence, 3),
            needs_confirmation=result.needs_confirmation,
        )
        return result

    async def _run_cascade(
        self,
        raw_name: str,
        norm: str,
        supplier_id: uuid.UUID | None,
        supplier_name: str | None,
        unit: str | None,
    ) -> ResolutionResult:
        """Execute steps 1-5 in order, stopping at first sufficient match."""

        # ── Step 1: Exact alias lookup ─────────────────────────────────────────
        alias = await product_repo.find_alias_exact(self.session, norm, supplier_id)
        if alias and alias.is_confirmed:
            product = await product_repo.get_product_by_id(self.session, alias.product_id)
            if product:
                log.debug("resolved_via_exact_alias", alias=alias.alias)
                return ResolutionResult(
                    raw_name=raw_name,
                    product=product,
                    confidence=min(alias.confidence, 1.0),
                    method=ResolutionMethod.exact_alias,
                    alias_id=alias.id,
                )

        # ── Step 2: Normalised product name match ──────────────────────────────
        product = await product_repo.find_product_by_normalised_name(self.session, norm)
        if product:
            log.debug("resolved_via_normalised_name", product=product.canonical_name)
            return ResolutionResult(
                raw_name=raw_name,
                product=product,
                confidence=0.95,
                method=ResolutionMethod.normalized,
            )

        # ── Step 3: Fuzzy matching ─────────────────────────────────────────────
        result = await self._fuzzy_resolve(raw_name, norm, supplier_id)
        if result:
            return result

        # ── Step 4: pgvector semantic similarity ───────────────────────────────
        result = await self._vector_resolve(raw_name, supplier_id, unit)
        if result:
            return result

        # ── Step 5: LLM contextual reasoning ──────────────────────────────────
        result = await self._llm_resolve(raw_name, supplier_name, unit)
        if result:
            return result

        # ── Unresolved ─────────────────────────────────────────────────────────
        log.info("product_unresolved", raw_name=raw_name)
        return ResolutionResult(
            raw_name=raw_name,
            product=None,
            confidence=0.0,
            method=ResolutionMethod.unresolved,
            needs_confirmation=True,
            notes="No match found after full resolution cascade.",
        )

    async def _fuzzy_resolve(
        self,
        raw_name: str,
        norm: str,
        supplier_id: uuid.UUID | None,
    ) -> ResolutionResult | None:
        """Step 3 — RapidFuzz against all known product names and aliases."""
        threshold = self._settings.fuzzy_match_threshold

        # Build candidate name → Product map
        all_products = await product_repo.get_all_products(self.session)
        if not all_products:
            return None

        # Index: normalised_name → Product
        name_to_product: dict[str, Product] = {
            p.normalized_name: p for p in all_products
        }
        # Also include canonical names
        canonical_to_product: dict[str, Product] = {
            p.canonical_name.lower(): p for p in all_products
        }

        # Also include confirmed aliases
        all_aliases = await product_repo.get_all_aliases(self.session)
        alias_to_product: dict[str, Product] = {}
        for a in all_aliases:
            prod = name_to_product.get(
                next((p.normalized_name for p in all_products if p.id == a.product_id), ""),
                None,
            )
            if prod:
                alias_to_product[a.normalized_alias] = prod

        combined: dict[str, Product] = {
            **name_to_product,
            **canonical_to_product,
            **alias_to_product,
        }

        if not combined:
            return None

        # Run token_sort_ratio for robustness to word-order differences
        match = process.extractOne(
            norm,
            combined.keys(),
            scorer=fuzz.token_sort_ratio,
            score_cutoff=threshold,
        )

        if not match:
            log.debug("fuzzy_no_match", raw_name=raw_name, threshold=threshold)
            return None

        matched_key, score, _ = match
        product = combined[matched_key]
        confidence = score / 100.0

        log.debug(
            "resolved_via_fuzzy",
            raw_name=raw_name,
            matched_key=matched_key,
            score=score,
            product=product.canonical_name,
        )
        return ResolutionResult(
            raw_name=raw_name,
            product=product,
            confidence=confidence,
            method=ResolutionMethod.fuzzy,
            needs_confirmation=confidence < 0.92,
        )

    async def _vector_resolve(
        self,
        raw_name: str,
        supplier_id: uuid.UUID | None,
        unit: str | None,
    ) -> ResolutionResult | None:
        """Step 4 — pgvector cosine similarity search."""
        try:
            embedding = await embed_text(raw_name)
        except (AIServiceError, Exception) as exc:
            log.warning("vector_embedding_failed", raw_name=raw_name, error=str(exc))
            return None

        try:
            hits = await product_repo.search_products_by_vector(
                self.session, embedding, limit=3
            )
        except Exception as exc:
            log.warning("vector_search_failed", raw_name=raw_name, error=str(exc))
            return None

        if not hits:
            return None

        best_product, best_distance = hits[0]
        # Convert cosine distance [0,2] → confidence [0,1]
        confidence = max(0.0, 1.0 - best_distance / 2.0)

        # Only use if meaningfully better than alternatives
        if confidence < 0.70:
            log.debug("vector_match_too_weak", raw_name=raw_name, confidence=confidence)
            return None

        candidates = [p for p, _ in hits[1:]]
        needs_confirmation = len(hits) > 1 and confidence < 0.88

        log.debug(
            "resolved_via_vector",
            raw_name=raw_name,
            product=best_product.canonical_name,
            confidence=round(confidence, 3),
        )
        return ResolutionResult(
            raw_name=raw_name,
            product=best_product,
            confidence=confidence,
            method=ResolutionMethod.vector,
            candidates=candidates,
            needs_confirmation=needs_confirmation,
        )

    async def _llm_resolve(
        self,
        raw_name: str,
        supplier_name: str | None,
        unit: str | None,
    ) -> ResolutionResult | None:
        """Step 5 — LLM disambiguation against top fuzzy candidates."""
        all_products = await product_repo.get_all_products(self.session)
        if not all_products:
            return None

        # Give LLM the top-5 fuzzy candidates even below threshold
        norm = product_repo.normalise_product_name(raw_name)
        name_map: dict[str, Product] = {p.normalized_name: p for p in all_products}
        if not name_map:
            return None

        top_matches = process.extract(
            norm,
            name_map.keys(),
            scorer=fuzz.token_sort_ratio,
            limit=5,
        )
        if not top_matches:
            return None

        candidates = [name_map[key] for key, score, _ in top_matches if score > 40]
        if not candidates:
            return None

        product, confidence, reason = await _llm_disambiguate(
            raw_name=raw_name,
            candidates=candidates,
            supplier_name=supplier_name,
            unit=unit,
        )

        if not product or confidence < 0.65:
            return None

        log.debug(
            "resolved_via_llm",
            raw_name=raw_name,
            product=product.canonical_name,
            confidence=round(confidence, 3),
            reason=reason,
        )
        return ResolutionResult(
            raw_name=raw_name,
            product=product,
            confidence=confidence,
            method=ResolutionMethod.llm,
            needs_confirmation=confidence < 0.85,
            notes=reason,
        )

    async def resolve_batch(
        self,
        items: list[dict],
        supplier_id: uuid.UUID | None = None,
        supplier_name: str | None = None,
    ) -> list[ResolutionResult]:
        """
        Resolve a list of receipt line items.

        Args:
            items: List of dicts with keys: raw_name, unit (optional).
        """
        results = []
        for item in items:
            result = await self.resolve(
                raw_name=item["raw_name"],
                supplier_id=supplier_id,
                supplier_name=supplier_name,
                unit=item.get("unit"),
            )
            results.append(result)
        return results
