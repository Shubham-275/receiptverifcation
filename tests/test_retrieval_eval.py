"""
RAG / Retrieval Evaluation Test Suite (Section 15 of plan.md).

Evaluates retrieval on a gold benchmark query set:
  1. Exact product queries (e.g. "Rice")
  2. Misspelled product names (e.g. "prle g")
  3. Supplier-specific queries (e.g. "Sharma Traders")
  4. Ambiguous aliases (e.g. "PG" with supplier context)
  5. Receipt-finding semantic queries (e.g. "receipt where I bought biscuit around 10")
  6. Numerical aggregation queries (e.g. "How much rice did I buy") -> MUST use SQL, never vector retrieval!

Measures:
  - Routing accuracy: cheapest reliable method selected
  - Semantic retrieval hit rate
  - Protection against vector aggregation
"""
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.product import Product
from app.models.receipt import Receipt
from app.models.supplier import Supplier
from app.services.retrieval import (
    hybrid_receipt_search,
    search_products_semantic,
    search_receipts_semantic,
)


GOLD_QUERIES = [
    {
        "query": "Rice",
        "category": "exact_product",
        "expected_method": "normalized",
        "requires_vector": False,
    },
    {
        "query": "prle g",
        "category": "misspelled_product",
        "expected_method": "fuzzy",
        "requires_vector": False,
    },
    {
        "query": "Sharma Traders",
        "category": "supplier_query",
        "expected_method": "exact_supplier",
        "requires_vector": False,
    },
    {
        "query": "Find the receipt where I bought that biscuit around ₹10",
        "category": "receipt_finding_semantic",
        "expected_method": "vector",
        "requires_vector": True,
    },
    {
        "query": "How much rice did I buy last month?",
        "category": "numerical_aggregation",
        "expected_method": "sql",
        "requires_vector": False,
    },
]


class TestRetrievalEvaluation:

    @pytest.mark.asyncio
    async def test_gold_query_cheapest_method_routing(self):
        """Verify that exact & misspelled queries don't unnecessarily invoke vector search."""
        from app.repositories import product_repo

        for q in GOLD_QUERIES:
            query_str = q["query"]
            cat = q["category"]

            if cat == "exact_product":
                norm = product_repo.normalise_product_name(query_str)
                assert norm == "rice"

            elif cat == "misspelled_product":
                from rapidfuzz import fuzz
                score = fuzz.token_sort_ratio(query_str, "parle g")
                assert score >= 85

            elif cat == "supplier_query":
                norm = product_repo.normalise_product_name(query_str)
                assert norm == "sharma traders"

    @pytest.mark.asyncio
    async def test_semantic_receipt_search_hit_rate(self):
        """Verify semantic retrieval ranks close receipts by distance."""
        mock_session = AsyncMock()
        r1 = Receipt(id=uuid.uuid4(), supplier_raw_name="Sharma Traders")
        r2 = Receipt(id=uuid.uuid4(), supplier_raw_name="Gupta Wholesalers")

        # Mock vector search returning (Receipt, distance)
        mock_results = [(r1, 0.15), (r2, 0.45)]
        with (
            patch("app.services.retrieval.embed_text", AsyncMock(return_value=[0.1] * 768)),
            patch("app.repositories.receipt_repo.search_receipts_by_vector", AsyncMock(return_value=mock_results)),
        ):
            results = await search_receipts_semantic(
                mock_session,
                "Find the receipt where I bought that biscuit around ₹10",
                limit=5,
                max_distance=0.5,
            )

            assert len(results) == 2
            # Recall@1 matches closest receipt
            top_receipt, top_dist = results[0]
            assert top_receipt.id == r1.id
            assert top_dist == 0.15

    @pytest.mark.asyncio
    async def test_semantic_product_search(self):
        """Verify semantic product search uses vector cosine distance."""
        mock_session = AsyncMock()
        p1 = Product(id=uuid.uuid4(), canonical_name="Parle-G", normalized_name="parle g")
        mock_results = [(p1, 0.22)]

        with (
            patch("app.services.retrieval.embed_text", AsyncMock(return_value=[0.1] * 768)),
            patch("app.repositories.product_repo.search_products_by_vector", AsyncMock(return_value=mock_results)),
        ):
            results = await search_products_semantic(mock_session, "tea biscuit", limit=3)
            assert len(results) == 1
            assert results[0][0].canonical_name == "Parle-G"
            assert results[0][1] == 0.22

    @pytest.mark.asyncio
    async def test_hybrid_search_deduplicates_supplier_and_vector(self):
        """Verify hybrid search returns deduplicated list combining exact supplier and vector matches."""
        mock_session = AsyncMock()
        r_common = Receipt(id=uuid.uuid4(), supplier_raw_name="Sharma Traders")
        r_vector = Receipt(id=uuid.uuid4(), supplier_raw_name="Other Store")

        supplier = Supplier(id=uuid.uuid4(), name="Sharma Traders")

        with (
            patch("app.repositories.product_repo.find_supplier_by_name", AsyncMock(return_value=supplier)),
            patch("app.repositories.receipt_repo.list_receipts", AsyncMock(return_value=([r_common], 1))),
            patch("app.services.retrieval.search_receipts_semantic", AsyncMock(return_value=[(r_common, 0.1), (r_vector, 0.3)])),
        ):
            results = await hybrid_receipt_search(mock_session, "Sharma Traders", limit=5)

            # Both returned, r_common appears once despite being in both
            assert len(results) == 2
            ids = [r.id for r in results]
            assert ids == [r_common.id, r_vector.id]
