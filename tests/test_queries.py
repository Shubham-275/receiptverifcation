"""
Tests for Phase 9 — LLM Query Layer.

Covers:
  - Route classification (SQL, RAG, SQL+RAG)
  - Natural answer generation formatting
  - Product query extraction helper
  - End-to-end process_user_query execution
  - POST /api/v1/queries endpoint
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.receipt import Receipt
from app.services.query_engine import (
    RouteType,
    _extract_product_name_from_query,
    classify_route,
    generate_natural_answer,
    process_user_query,
)


class TestRouteClassification:

    def test_sql_route_for_quantity(self):
        route = classify_route("How much rice did I buy last month?")
        assert route == RouteType.sql

    def test_sql_route_for_average_price(self):
        route = classify_route("What was my average buying price for Harpic in the last 9 months?")
        assert route == RouteType.sql

    def test_sql_route_for_cheapest_supplier(self):
        route = classify_route("Which supplier gave me the cheapest Parle-G?")
        assert route == RouteType.sql

    def test_rag_route_for_receipt_finding(self):
        route = classify_route("Find the receipt where I bought that biscuit around ₹10")
        assert route == RouteType.rag

    def test_sql_and_rag_for_combined_query(self):
        route = classify_route("Find the rice receipts from Sharma Traders and tell me whether my buying price increased")
        assert route == RouteType.sql_and_rag


class TestProductExtraction:

    def test_extract_how_much(self):
        name = _extract_product_name_from_query("How much rice did I buy last month?")
        assert "rice" in name.lower()

    def test_extract_average_price_for(self):
        name = _extract_product_name_from_query("What was my average buying price for Harpic?")
        assert "harpic" in name.lower()

    def test_extract_cheapest(self):
        name = _extract_product_name_from_query("Which supplier gave me the cheapest Parle-G?")
        assert "parle-g" in name.lower() or "parle g" in name.lower()


class TestAnswerGeneration:

    def test_sql_quantity_answer(self):
        data = {
            "product_query": "Rice",
            "total_quantity": 25.0,
            "unit": "kg",
            "total_spend": 1850.0,
            "avg_unit_price": 74.0,
            "purchase_count": 1,
        }
        ans = generate_natural_answer("How much rice did I buy?", RouteType.sql, data, [])
        assert "25 kg" in ans
        assert "₹1,850.00" in ans
        assert "Rice" in ans

    def test_sql_supplier_comparison_answer(self):
        data = {
            "product_query": "Parle-G",
            "supplier_comparison": [
                {
                    "supplier_name": "Sharma Traders",
                    "avg_price": 9.50,
                    "unit": "pkt",
                },
                {
                    "supplier_name": "Gupta Wholesalers",
                    "avg_price": 10.00,
                    "unit": "pkt",
                },
            ],
        }
        ans = generate_natural_answer("Which supplier gave me the cheapest Parle-G?", RouteType.sql, data, [])
        assert "Sharma Traders" in ans
        assert "₹9.50" in ans

    def test_rag_receipt_answer(self):
        r = MagicMock()
        r.receipt_date = None
        r.supplier_raw_name = "Sharma Traders"
        r.receipt_total = 1850.0
        r.purchase_items = []
        ans = generate_natural_answer("Find receipt", RouteType.rag, None, [r])
        assert "Sharma Traders" in ans
        assert "₹1,850.00" in ans


class TestQueryEndpoint:

    def test_query_endpoint_sql_success(self, client):
        mock_resp = MagicMock()
        mock_resp.query = "How much rice did I buy?"
        mock_resp.route = "sql"
        mock_resp.answer = "You bought 25 kg of Rice for ₹1,850.00."
        mock_resp.structured_data = {"total_quantity": 25.0}
        mock_resp.receipts = []
        mock_resp.execution_time_ms = 12.5

        with patch("app.api.queries.process_user_query", AsyncMock(return_value=mock_resp)):
            resp = client.post("/api/v1/queries", json={"query": "How much rice did I buy?"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["route"] == "sql"
        assert "25 kg" in data["answer"]
        assert data["execution_time_ms"] > 0
