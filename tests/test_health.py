"""
Tests for the health and dashboard endpoints (Checkpoint 1 & 22).
These tests do NOT require a live database.
"""
from fastapi.testclient import TestClient


def test_liveness(client: TestClient) -> None:
    """GET /api/v1/health should return 200 with status=ok."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "environment" in body


def test_readiness_returns_200(client: TestClient) -> None:
    """
    GET /api/v1/health/ready should return 200.
    Database will be unavailable in CI without Postgres, so we only
    check that the endpoint responds — not that db=ok.
    """
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("ok", "degraded")
    assert body["database"] in ("ok", "unavailable")


def test_docs_available(client: TestClient) -> None:
    """OpenAPI docs should be reachable."""
    response = client.get("/docs")
    assert response.status_code == 200


def test_openapi_schema(client: TestClient) -> None:
    """OpenAPI JSON schema should be valid and list known paths."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    paths = schema.get("paths", {})
    assert "/api/v1/health" in paths
    assert "/api/v1/health/ready" in paths
    assert "/api/v1/receipts/upload" in paths
    assert "/api/v1/queries" in paths
    assert "/api/v1/analytics/overview" in paths


def test_dashboard_served_at_root(client: TestClient) -> None:
    """Dashboard HTML should be served at /."""
    response = client.get("/")
    assert response.status_code == 200
    assert "Receipt Intelligence" in response.text
    assert "Scan a receipt" in response.text


def test_dashboard_served_at_dashboard_path(client: TestClient) -> None:
    """Dashboard HTML should be served at /dashboard."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "Receipt Intelligence" in response.text
