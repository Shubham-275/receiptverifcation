"""
Shared pytest fixtures and configuration.
"""
import os

import pytest
from fastapi.testclient import TestClient

# Point at a test database so tests never touch the real DB
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/receiptdb_test",
)
os.environ.setdefault(
    "DATABASE_URL_SYNC",
    "postgresql+psycopg2://postgres:postgres@localhost:5432/receiptdb_test",
)
os.environ.setdefault("ENVIRONMENT", "development")
os.environ["LLM_PROVIDER"] = "gemini"
os.environ["GEMINI_API_KEY"] = "test-key"
os.environ["AUTH_SEED_ENABLED"] = "false"


@pytest.fixture(scope="session")
def client():
    """Synchronous test client with an authenticated user pre-injected.

    Auth itself is covered in tests/test_auth.py, which uses a client without
    this override. The rest of the suite focuses on ledger behaviour.
    """
    # Import app after env vars are set
    from app.api.auth import require_user
    from app.main import app
    from types import SimpleNamespace

    app.dependency_overrides[require_user] = lambda: SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        username="tester",
        display_name="Tester",
        is_active=True,
    )

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.pop(require_user, None)
