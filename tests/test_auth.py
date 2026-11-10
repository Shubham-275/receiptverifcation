"""Authentication tests — password hashing, signed sessions, and API flows."""
from __future__ import annotations

import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.models.user import User
from app.services.auth import (
    SESSION_COOKIE,
    create_session_token,
    hash_password,
    normalize_username,
    validate_username,
    verify_password,
    verify_session_token,
)


@pytest.fixture()
def auth_client():
    """A client without the authenticated dependency override."""
    from app.api.auth import require_user
    from app.main import app

    previous = app.dependency_overrides.pop(require_user, None)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    if previous is not None:
        app.dependency_overrides[require_user] = previous


def make_user(*, password: str = "ledger2026", active: bool = True) -> User:
    return User(
        id=uuid.uuid4(),
        username="meera",
        display_name="Meera Stores",
        password_hash=hash_password(password),
        is_active=active,
        last_login_at=None,
    )


# ── Primitives ────────────────────────────────────────────────────────────────


def test_password_hash_roundtrip() -> None:
    encoded = hash_password("ledger2026")
    assert "ledger2026" not in encoded
    assert verify_password("ledger2026", encoded) is True
    assert verify_password("wrong-password", encoded) is False
    assert verify_password("ledger2026", None) is False
    assert verify_password("ledger2026", "not-a-hash") is False


def test_username_normalisation() -> None:
    assert normalize_username("  Meera.Store ") == "meera.store"
    assert validate_username("Meera_Store") == "meera_store"
    with pytest.raises(ValueError):
        validate_username("ab")
    with pytest.raises(ValueError):
        validate_username("has space")


def test_session_token_roundtrip() -> None:
    user_id = uuid.uuid4()
    token = create_session_token(user_id)
    assert verify_session_token(token) == user_id
    assert verify_session_token(token + "tampered") is None
    assert verify_session_token(None) is None
    assert verify_session_token("garbage") is None


def test_session_token_expiry() -> None:
    user_id = uuid.uuid4()
    token = create_session_token(user_id, ttl_hours=1)
    with patch("app.services.auth.time.time", return_value=time.time() + 7200):
        assert verify_session_token(token) is None


# ── Status ───────────────────────────────────────────────────────────────────


def test_status_requires_setup(auth_client: TestClient) -> None:
    with patch(
        "app.repositories.user_repo.count_users", AsyncMock(return_value=0)
    ):
        response = auth_client.get("/api/v1/auth/status")

    assert response.status_code == 200
    assert response.json() == {
        "setup_required": True,
        "authenticated": False,
        "user": None,
    }


def test_status_reports_signed_in_user(auth_client: TestClient) -> None:
    user = make_user()
    auth_client.cookies.set(SESSION_COOKIE, create_session_token(user.id))
    with patch(
        "app.repositories.user_repo.get_by_id", AsyncMock(return_value=user)
    ):
        response = auth_client.get("/api/v1/auth/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["authenticated"] is True
    assert payload["setup_required"] is False
    assert payload["user"]["username"] == "meera"


# ── Setup ────────────────────────────────────────────────────────────────────


def test_setup_creates_first_owner(auth_client: TestClient) -> None:
    user = make_user()
    with (
        patch("app.repositories.user_repo.count_users", AsyncMock(return_value=0)),
        patch(
            "app.repositories.user_repo.create_user",
            AsyncMock(return_value=user),
        ),
        patch(
            "app.repositories.user_repo.touch_last_login", AsyncMock()
        ),
    ):
        response = auth_client.post(
            "/api/v1/auth/setup",
            json={
                "username": "Meera",
                "password": "ledger2026",
                "display_name": "Meera Stores",
            },
        )

    assert response.status_code == 201
    assert response.json()["username"] == "meera"
    token = response.cookies.get(SESSION_COOKIE)
    assert token and verify_session_token(token) == user.id


def test_setup_rejected_when_owner_exists(auth_client: TestClient) -> None:
    with patch(
        "app.repositories.user_repo.count_users", AsyncMock(return_value=1)
    ):
        response = auth_client.post(
            "/api/v1/auth/setup",
            json={"username": "meera", "password": "ledger2026"},
        )

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_setup_enforces_password_length(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/auth/setup",
        json={"username": "meera", "password": "short"},
    )
    assert response.status_code == 422


# ── Login / logout ───────────────────────────────────────────────────────────


def test_login_sets_session_cookie(auth_client: TestClient) -> None:
    user = make_user()
    with (
        patch(
            "app.repositories.user_repo.get_by_username",
            AsyncMock(return_value=user),
        ),
        patch("app.repositories.user_repo.touch_last_login", AsyncMock()),
    ):
        response = auth_client.post(
            "/api/v1/auth/login",
            json={"username": "Meera", "password": "ledger2026"},
        )

    assert response.status_code == 200
    assert response.json()["display_name"] == "Meera Stores"
    token = response.cookies.get(SESSION_COOKIE)
    assert token and verify_session_token(token) == user.id
    assert "httponly" in response.headers["set-cookie"].lower()


def test_login_rejects_wrong_password(auth_client: TestClient) -> None:
    user = make_user()
    with patch(
        "app.repositories.user_repo.get_by_username", AsyncMock(return_value=user)
    ):
        response = auth_client.post(
            "/api/v1/auth/login",
            json={"username": "meera", "password": "not-the-password"},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect username or password."


def test_login_rejects_unknown_user(auth_client: TestClient) -> None:
    with patch(
        "app.repositories.user_repo.get_by_username", AsyncMock(return_value=None)
    ):
        response = auth_client.post(
            "/api/v1/auth/login",
            json={"username": "nobody", "password": "ledger2026"},
        )

    assert response.status_code == 401


def test_login_rejects_disabled_account(auth_client: TestClient) -> None:
    user = make_user(active=False)
    with patch(
        "app.repositories.user_repo.get_by_username", AsyncMock(return_value=user)
    ):
        response = auth_client.post(
            "/api/v1/auth/login",
            json={"username": "meera", "password": "ledger2026"},
        )

    assert response.status_code == 403


def test_logout_clears_cookie(auth_client: TestClient) -> None:
    response = auth_client.post("/api/v1/auth/logout")

    assert response.status_code == 204
    cookie = response.headers["set-cookie"].lower()
    assert "ri_session=" in cookie
    assert "max-age=0" in cookie or "expires=" in cookie


def test_logout_form_redirects_to_access_screen(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/v1/auth/logout",
        headers={"Accept": "text/html,application/xhtml+xml"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"].lower()
    assert "ri_session=" in cookie
    assert "max-age=0" in cookie or "expires=" in cookie


# ── Protection ───────────────────────────────────────────────────────────────


def test_protected_endpoint_requires_session(auth_client: TestClient) -> None:
    response = auth_client.get("/api/v1/analytics/overview")

    assert response.status_code == 401
    assert "sign in" in response.json()["detail"].lower()


def test_me_returns_signed_in_user(auth_client: TestClient) -> None:
    user = make_user()
    auth_client.cookies.set(SESSION_COOKIE, create_session_token(user.id))
    with patch(
        "app.repositories.user_repo.get_by_id", AsyncMock(return_value=user)
    ):
        response = auth_client.get("/api/v1/auth/me")

    assert response.status_code == 200
    assert response.json()["username"] == "meera"


def test_me_rejects_expired_session(auth_client: TestClient) -> None:
    user = make_user()
    token = create_session_token(user.id, ttl_hours=1)
    auth_client.cookies.set(SESSION_COOKIE, token)
    with patch(
        "app.repositories.user_repo.get_by_id",
        AsyncMock(return_value=SimpleNamespace(is_active=True)),
    ):
        with patch("app.services.auth.time.time", return_value=time.time() + 7200):
            response = auth_client.get("/api/v1/auth/me")

    assert response.status_code == 401
