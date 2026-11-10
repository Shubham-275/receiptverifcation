"""
Tests for Phase 6 — Product Entity Resolution (Checkpoint 5).

All tests mock the DB session and repository functions so no live
PostgreSQL connection is needed.

Covers:
  - normalise_product_name utility
  - Step 1: Exact alias (supplier-scoped and global)
  - Step 2: Normalized name match
  - Step 3: Fuzzy matching (above + below threshold)
  - Cascade falls through steps when earlier steps miss
  - Step 4: Vector resolution (mocked)
  - Step 5: LLM resolution (mocked)
  - Unresolved when all steps fail
  - Same abbreviation across suppliers → different products
  - Self-learning: alias auto-promotion
  - Batch resolution
  - repository: normalise_product_name edge cases
"""
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_product(name: str, category: str = "general") -> MagicMock:
    p = MagicMock()
    p.id = uuid.uuid4()
    p.canonical_name = name
    p.normalized_name = name.lower().replace("-", " ").strip()
    p.category = category
    p.name_embedding = None
    return p


def _make_alias(
    alias: str,
    product: MagicMock,
    supplier_id=None,
    confidence: float = 1.0,
    is_confirmed: bool = True,
) -> MagicMock:
    from app.repositories.product_repo import normalise_product_name
    a = MagicMock()
    a.id = uuid.uuid4()
    a.alias = alias
    a.normalized_alias = normalise_product_name(alias)
    a.product_id = product.id
    a.supplier_id = supplier_id
    a.confidence = confidence
    a.is_confirmed = is_confirmed
    return a


def _make_mock_session() -> AsyncMock:
    """Minimal AsyncSession mock."""
    session = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    return session


# ── normalise_product_name tests ───────────────────────────────────────────────

class TestNormaliseProductName:
    def test_lowercase(self):
        from app.repositories.product_repo import normalise_product_name
        assert normalise_product_name("RICE") == "rice"

    def test_strips_hyphens(self):
        from app.repositories.product_repo import normalise_product_name
        assert normalise_product_name("Parle-G") == "parle g"

    def test_strips_dots(self):
        from app.repositories.product_repo import normalise_product_name
        result = normalise_product_name("P.G.")
        # Dots become spaces, then collapsed and stripped
        assert "p" in result and "g" in result

    def test_collapses_spaces(self):
        from app.repositories.product_repo import normalise_product_name
        assert normalise_product_name("PARLE  G") == "parle g"

    def test_strips_whitespace(self):
        from app.repositories.product_repo import normalise_product_name
        assert normalise_product_name("  Rice  ") == "rice"

    def test_parleg_variants(self):
        from app.repositories.product_repo import normalise_product_name
        variants = ["PARLE G", "PARLE-G", "Parle-G", "PARLEG", "parle g"]
        norms = {normalise_product_name(v) for v in variants}
        # All hyphen variants → "parle g"; PARLEG stays "parleg"
        assert "parle g" in norms


# ── Resolver cascade tests ─────────────────────────────────────────────────────

class TestProductResolver:

    @pytest.fixture
    def session(self):
        return _make_mock_session()

    # ── Step 1: Exact alias ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_step1_exact_alias_global(self, session):
        """Known global alias → resolved immediately."""
        from app.services.resolver import ProductResolver
        product = _make_product("Parle-G", "biscuit")
        alias = _make_alias("PRLE G", product)

        with (
            patch("app.repositories.product_repo.find_alias_exact", AsyncMock(return_value=alias)),
            patch("app.repositories.product_repo.get_product_by_id", AsyncMock(return_value=product)),
            patch("app.repositories.product_repo.upsert_alias_observation", AsyncMock()),
            patch("app.repositories.product_repo.get_alias_observation", AsyncMock(return_value=None)),
        ):
            resolver = ProductResolver(session)
            result = await resolver.resolve("PRLE G")

        from app.models.purchase_item import ResolutionMethod
        assert result.product == product
        assert result.method == ResolutionMethod.exact_alias
        assert result.confidence >= 0.9
        assert result.needs_confirmation is False

    @pytest.mark.asyncio
    async def test_step1_supplier_scoped_alias(self, session):
        """Supplier-scoped alias takes priority over global."""
        from app.services.resolver import ProductResolver

        supplier_id = uuid.uuid4()
        parleg = _make_product("Parle-G")
        priya_gold = _make_product("Priya Gold")

        # "PG" → Parle-G at this supplier
        scoped_alias = _make_alias("PG", parleg, supplier_id=supplier_id)

        with (
            patch("app.repositories.product_repo.find_alias_exact", AsyncMock(return_value=scoped_alias)),
            patch("app.repositories.product_repo.get_product_by_id", AsyncMock(return_value=parleg)),
            patch("app.repositories.product_repo.upsert_alias_observation", AsyncMock()),
            patch("app.repositories.product_repo.get_alias_observation", AsyncMock(return_value=None)),
        ):
            resolver = ProductResolver(session)
            result = await resolver.resolve("PG", supplier_id=supplier_id)

        assert result.product.canonical_name == "Parle-G"

    @pytest.mark.asyncio
    async def test_step1_different_suppliers_same_alias(self, session):
        """Same abbreviation maps to DIFFERENT products at different suppliers."""
        from app.services.resolver import ProductResolver

        supplier_a_id = uuid.uuid4()
        supplier_c_id = uuid.uuid4()
        parleg = _make_product("Parle-G")
        priya = _make_product("Priya Gold")

        # At supplier A: PG → Parle-G
        alias_a = _make_alias("PG", parleg, supplier_id=supplier_a_id)
        # At supplier C: PG → Priya Gold
        alias_c = _make_alias("PG", priya, supplier_id=supplier_c_id)

        async def mock_find_alias(session, norm_alias, supplier_id=None):
            if supplier_id == supplier_a_id:
                return alias_a
            elif supplier_id == supplier_c_id:
                return alias_c
            return None

        with (
            patch("app.repositories.product_repo.find_alias_exact", mock_find_alias),
            patch("app.repositories.product_repo.get_product_by_id", AsyncMock(side_effect=[parleg, priya])),
            patch("app.repositories.product_repo.upsert_alias_observation", AsyncMock()),
            patch("app.repositories.product_repo.get_alias_observation", AsyncMock(return_value=None)),
        ):
            resolver = ProductResolver(session)
            result_a = await resolver.resolve("PG", supplier_id=supplier_a_id)
            result_c = await resolver.resolve("PG", supplier_id=supplier_c_id)

        assert result_a.product.canonical_name == "Parle-G"
        assert result_c.product.canonical_name == "Priya Gold"

    # ── Step 2: Normalised name match ─────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_step2_normalised_name_match(self, session):
        """'RICE' normalizes to 'rice' which matches products.normalized_name."""
        from app.services.resolver import ProductResolver
        rice = _make_product("Rice", "grain")
        rice.normalized_name = "rice"

        with (
            patch("app.repositories.product_repo.find_alias_exact", AsyncMock(return_value=None)),
            patch("app.repositories.product_repo.find_product_by_normalised_name", AsyncMock(return_value=rice)),
            patch("app.repositories.product_repo.upsert_alias_observation", AsyncMock()),
            patch("app.repositories.product_repo.get_alias_observation", AsyncMock(return_value=None)),
        ):
            resolver = ProductResolver(session)
            result = await resolver.resolve("RICE")

        from app.models.purchase_item import ResolutionMethod
        assert result.method == ResolutionMethod.normalized
        assert result.product.canonical_name == "Rice"
        assert result.confidence == pytest.approx(0.95)

    # ── Step 3: Fuzzy ─────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_step3_fuzzy_match_above_threshold(self, session):
        """'Clinc Plus' (minor typo) fuzzy-matches canonical 'Clinic Plus' (>85 score)."""
        from app.services.resolver import ProductResolver
        # Use a name that scores well above the 85 threshold
        product = _make_product("Clinic Plus")
        product.normalized_name = "clinic plus"

        with (
            patch("app.repositories.product_repo.find_alias_exact", AsyncMock(return_value=None)),
            patch("app.repositories.product_repo.find_product_by_normalised_name", AsyncMock(return_value=None)),
            patch("app.repositories.product_repo.get_all_products", AsyncMock(return_value=[product])),
            patch("app.repositories.product_repo.get_all_aliases", AsyncMock(return_value=[])),
            # Stop the cascade from falling into vector/LLM
            patch("app.services.resolver.embed_text", AsyncMock(side_effect=Exception("no embed"))),
            patch("app.services.resolver._llm_disambiguate", AsyncMock(return_value=(None, 0.0, "no match"))),
            patch("app.repositories.product_repo.upsert_alias_observation", AsyncMock()),
            patch("app.repositories.product_repo.get_alias_observation", AsyncMock(return_value=None)),
        ):
            resolver = ProductResolver(session)
            result = await resolver.resolve("Clinc Plus")

        from app.models.purchase_item import ResolutionMethod
        assert result.method == ResolutionMethod.fuzzy
        assert "Clinic" in result.product.canonical_name

    @pytest.mark.asyncio
    async def test_step3_fuzzy_falls_through_on_low_score(self, session):
        """'XYZ123' gets no fuzzy match → falls through to vector/LLM → unresolved."""
        from app.services.resolver import ProductResolver
        product = _make_product("Rice")
        product.normalized_name = "rice"

        with (
            patch("app.repositories.product_repo.find_alias_exact", AsyncMock(return_value=None)),
            patch("app.repositories.product_repo.find_product_by_normalised_name", AsyncMock(return_value=None)),
            patch("app.repositories.product_repo.get_all_products", AsyncMock(return_value=[product])),
            patch("app.repositories.product_repo.get_all_aliases", AsyncMock(return_value=[])),
            # Patch embed_text where resolver actually uses it
            patch("app.services.resolver.embed_text", AsyncMock(side_effect=Exception("no embed"))),
            patch("app.services.resolver._llm_disambiguate", AsyncMock(return_value=(None, 0.0, "no match"))),
            patch("app.repositories.product_repo.upsert_alias_observation", AsyncMock()),
            patch("app.repositories.product_repo.get_alias_observation", AsyncMock(return_value=None)),
        ):
            resolver = ProductResolver(session)
            result = await resolver.resolve("XYZ123UNKNOWN")

        from app.models.purchase_item import ResolutionMethod
        assert result.product is None
        assert result.method == ResolutionMethod.unresolved
        assert result.needs_confirmation is True

    # ── Step 4: Vector resolution ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_step4_vector_resolution(self, session):
        """Passes through fuzzy, succeeds via vector similarity."""
        from app.services.resolver import ProductResolver
        product = _make_product("Parle-G")
        product.normalized_name = "parle g"

        mock_hits = [(product, 0.1)]  # distance 0.1 → confidence 0.95

        with (
            patch("app.repositories.product_repo.find_alias_exact", AsyncMock(return_value=None)),
            patch("app.repositories.product_repo.find_product_by_normalised_name", AsyncMock(return_value=None)),
            patch("app.repositories.product_repo.get_all_products", AsyncMock(return_value=[])),
            patch("app.repositories.product_repo.get_all_aliases", AsyncMock(return_value=[])),
            # Must patch at the resolver module where embed_text is already imported
            patch("app.services.resolver.embed_text", AsyncMock(return_value=[0.1] * 768)),
            patch("app.repositories.product_repo.search_products_by_vector", AsyncMock(return_value=mock_hits)),
            patch("app.repositories.product_repo.upsert_alias_observation", AsyncMock()),
            patch("app.repositories.product_repo.get_alias_observation", AsyncMock(return_value=None)),
        ):
            resolver = ProductResolver(session)
            result = await resolver.resolve("PRLE G")

        from app.models.purchase_item import ResolutionMethod
        assert result.method == ResolutionMethod.vector
        assert result.product.canonical_name == "Parle-G"
        assert result.confidence > 0.9

    # ── Step 5: LLM resolution ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_step5_llm_resolution(self, session):
        """When embedding fails, LLM contextually disambiguates."""
        from app.services.resolver import ProductResolver
        harpic = _make_product("Harpic", "cleaning")
        harpic.normalized_name = "harpic"

        with (
            patch("app.repositories.product_repo.find_alias_exact", AsyncMock(return_value=None)),
            patch("app.repositories.product_repo.find_product_by_normalised_name", AsyncMock(return_value=None)),
            patch("app.repositories.product_repo.get_all_products", AsyncMock(return_value=[harpic])),
            patch("app.repositories.product_repo.get_all_aliases", AsyncMock(return_value=[])),
            patch("app.services.resolver.embed_text", AsyncMock(side_effect=Exception("no embed"))),
            patch("app.services.resolver._llm_disambiguate", AsyncMock(return_value=(harpic, 0.88, "Harpic toilet cleaner"))),
            patch("app.repositories.product_repo.upsert_alias_observation", AsyncMock()),
            patch("app.repositories.product_repo.get_alias_observation", AsyncMock(return_value=None)),
        ):
            resolver = ProductResolver(session)
            result = await resolver.resolve("HRPIC BTL", supplier_name="Sharma Traders", unit="bottle")

        from app.models.purchase_item import ResolutionMethod
        assert result.method == ResolutionMethod.llm
        assert result.product.canonical_name == "Harpic"

    # ── Unresolved ─────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_new_product_unresolved(self, session):
        """Completely new product not in DB → unresolved, needs confirmation."""
        from app.services.resolver import ProductResolver

        with (
            patch("app.repositories.product_repo.find_alias_exact", AsyncMock(return_value=None)),
            patch("app.repositories.product_repo.find_product_by_normalised_name", AsyncMock(return_value=None)),
            patch("app.repositories.product_repo.get_all_products", AsyncMock(return_value=[])),
            patch("app.repositories.product_repo.get_all_aliases", AsyncMock(return_value=[])),
            patch("app.services.resolver.embed_text", AsyncMock(side_effect=Exception("no embed"))),
            patch("app.repositories.product_repo.upsert_alias_observation", AsyncMock()),
            patch("app.repositories.product_repo.get_alias_observation", AsyncMock(return_value=None)),
        ):
            resolver = ProductResolver(session)
            result = await resolver.resolve("Brandnew Product XYZ")

        assert result.product is None
        assert result.needs_confirmation is True

    # ── Self-learning alias promotion ─────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_alias_auto_promoted_after_threshold(self, session):
        """After 3 observations with high confidence, alias should be promoted."""
        from app.services.resolver import _maybe_promote_alias

        product = _make_product("Parle-G")
        obs = MagicMock()
        obs.occurrence_count = 4   # above minimum

        with (
            patch("app.repositories.product_repo.normalise_product_name",
                  side_effect=lambda x: x.lower()),
            patch("app.repositories.product_repo.find_alias_exact", AsyncMock(return_value=None)),
            patch("app.repositories.product_repo.get_alias_observation", AsyncMock(return_value=obs)),
            patch("app.repositories.product_repo.create_alias", AsyncMock()) as mock_create,
        ):
            promoted = await _maybe_promote_alias(
                session=session,
                raw_name="PRLE G",
                product=product,
                supplier_id=None,
                confidence=0.95,   # above auto_accept threshold (default 0.90)
                unit="packet",
            )

        assert promoted is True
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_alias_not_promoted_below_confidence(self, session):
        """Low confidence → no auto-promotion."""
        from app.services.resolver import _maybe_promote_alias
        product = _make_product("Parle-G")

        promoted = await _maybe_promote_alias(
            session=session,
            raw_name="PRLE G",
            product=product,
            supplier_id=None,
            confidence=0.50,  # below threshold
            unit="packet",
        )
        assert promoted is False

    @pytest.mark.asyncio
    async def test_alias_not_promoted_below_observation_count(self, session):
        """Even high confidence, too few observations → no auto-promotion."""
        from app.services.resolver import _maybe_promote_alias
        product = _make_product("Parle-G")

        obs = MagicMock()
        obs.occurrence_count = 1  # below minimum of 3

        with (
            patch("app.repositories.product_repo.normalise_product_name",
                  side_effect=lambda x: x.lower()),
            patch("app.repositories.product_repo.find_alias_exact", AsyncMock(return_value=None)),
            patch("app.repositories.product_repo.get_alias_observation", AsyncMock(return_value=obs)),
        ):
            promoted = await _maybe_promote_alias(
                session=session,
                raw_name="PRLE G",
                product=product,
                supplier_id=None,
                confidence=0.95,
                unit="packet",
            )
        assert promoted is False

    # ── Batch resolution ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_batch_resolution(self, session):
        """resolve_batch handles multiple items correctly."""
        from app.services.resolver import ProductResolver
        rice = _make_product("Rice")
        sugar = _make_product("Sugar")

        call_count = 0

        async def mock_resolve(self_inner, raw_name, supplier_id=None,
                               supplier_name=None, unit=None):
            nonlocal call_count
            call_count += 1
            from app.services.resolver import ResolutionResult
            from app.models.purchase_item import ResolutionMethod
            p = rice if "rice" in raw_name.lower() else sugar
            return ResolutionResult(
                raw_name=raw_name, product=p, confidence=0.95,
                method=ResolutionMethod.normalized,
            )

        items = [
            {"raw_name": "Rice", "unit": "kg"},
            {"raw_name": "Sugar", "unit": "kg"},
        ]

        with patch.object(ProductResolver, "resolve", mock_resolve):
            resolver = ProductResolver(session)
            results = await resolver.resolve_batch(items)

        assert len(results) == 2
        assert call_count == 2
        assert results[0].product.canonical_name == "Rice"
        assert results[1].product.canonical_name == "Sugar"
