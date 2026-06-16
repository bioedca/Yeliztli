"""Tests for Authentication system (P4-21a).

Covers:
- T4-22a: Login with correct PIN returns session cookie, wrong PIN returns 401
- T4-22b: Expired session redirects to login (returns 401)
- T4-22c: All API endpoints return 401 without valid session (except /api/health)
- Password set/change/remove flows
- Auth status endpoint
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.auth import (
    clear_all_rate_limits,
    clear_all_sessions,
    create_session,
    hash_password,
    validate_session,
    verify_password,
)
from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.tables import reference_metadata

# ═══════════════════════════════════════════════════════════════════════
# Unit tests for auth module
# ═══════════════════════════════════════════════════════════════════════


class TestPasswordHashing:
    """Test bcrypt password hashing and verification."""

    def test_hash_and_verify(self) -> None:
        hashed = hash_password("mypin123")
        assert verify_password("mypin123", hashed)

    def test_wrong_password_fails(self) -> None:
        hashed = hash_password("correct")
        assert not verify_password("wrong", hashed)

    def test_different_hashes_for_same_password(self) -> None:
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # bcrypt uses random salt
        assert verify_password("same", h1)
        assert verify_password("same", h2)


class TestSessionManagement:
    """Test in-memory session store."""

    def setup_method(self) -> None:
        clear_all_sessions()

    def test_create_and_validate(self) -> None:
        sid = create_session()
        assert validate_session(sid)

    def test_invalid_session(self) -> None:
        assert not validate_session("nonexistent")

    def test_destroy_session(self) -> None:
        from backend.auth import destroy_session

        sid = create_session()
        assert validate_session(sid)
        destroy_session(sid)
        assert not validate_session(sid)

    def test_expired_session(self) -> None:
        from backend.auth import _sessions

        sid = create_session()
        # Backdate the session to 5 hours ago
        _sessions[sid] = _sessions[sid] - 5 * 3600
        assert not validate_session(sid, timeout_hours=4)

    def test_session_touch_on_validate(self) -> None:
        import time

        from backend.auth import _sessions

        sid = create_session()
        old_time = _sessions[sid]
        time.sleep(0.01)
        validate_session(sid)
        assert _sessions[sid] >= old_time

    def test_clear_all(self) -> None:
        from backend.auth import _get_session_count

        create_session()
        create_session()
        assert _get_session_count() == 2
        clear_all_sessions()
        assert _get_session_count() == 0

    def teardown_method(self) -> None:
        clear_all_sessions()


# ═══════════════════════════════════════════════════════════════════════
# Helper: create a test client with auth enabled
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def auth_client(tmp_data_dir: Path):
    """TestClient with auth enabled and password set."""
    clear_all_sessions()
    clear_all_rate_limits()
    password_hash = hash_password("testpin")
    settings = Settings(
        data_dir=tmp_data_dir,
        wal_mode=False,
        auth_enabled=True,
        auth_password_hash=password_hash,
    )

    ref_path = settings.reference_db_path
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(engine)
    engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.auth.get_settings", return_value=settings),
        patch("backend.api.routes.auth.get_settings", return_value=settings),
        # config.toml (incl. auth settings) lives in DEFAULT_DATA_DIR; isolate it
        # to the temp dir so _persist_auth_settings never writes the real home.
        patch("backend.config.DEFAULT_DATA_DIR", tmp_data_dir),
    ):
        reset_registry()
        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc

        reset_registry()
    clear_all_sessions()
    clear_all_rate_limits()


@pytest.fixture
def noauth_client(tmp_data_dir: Path):
    """TestClient with auth disabled."""
    clear_all_sessions()
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_path = settings.reference_db_path
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(engine)
    engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.auth.get_settings", return_value=settings),
        patch("backend.api.routes.auth.get_settings", return_value=settings),
        # config.toml (incl. auth settings) lives in DEFAULT_DATA_DIR; isolate it
        # to the temp dir so _persist_auth_settings never writes the real home.
        patch("backend.config.DEFAULT_DATA_DIR", tmp_data_dir),
    ):
        reset_registry()
        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc

        reset_registry()
    clear_all_sessions()


# ═══════════════════════════════════════════════════════════════════════
# T4-22a: Login with correct/wrong PIN
# ═══════════════════════════════════════════════════════════════════════


class TestLogin:
    """T4-22a: Login with correct PIN returns session cookie, wrong PIN returns 401."""

    def test_correct_password_returns_session_cookie(self, auth_client: TestClient) -> None:
        resp = auth_client.post(
            "/api/auth/login",
            json={"password": "testpin"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert "gi_session" in resp.cookies

    def test_wrong_password_returns_401(self, auth_client: TestClient) -> None:
        resp = auth_client.post(
            "/api/auth/login",
            json={"password": "wrongpin"},
        )
        assert resp.status_code == 401
        assert "gi_session" not in resp.cookies

    def test_empty_password_returns_422(self, auth_client: TestClient) -> None:
        resp = auth_client.post(
            "/api/auth/login",
            json={"password": ""},
        )
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════
# T4-22b: Expired session returns 401
# ═══════════════════════════════════════════════════════════════════════


class TestExpiredSession:
    """T4-22b: Expired session (4h inactivity) returns 401."""

    def test_expired_session_returns_401(self, auth_client: TestClient) -> None:
        from backend.auth import _sessions

        # Login first
        resp = auth_client.post("/api/auth/login", json={"password": "testpin"})
        assert resp.status_code == 200
        session_cookie = resp.cookies.get("gi_session")

        # Backdate the session (the login cookie is already on the client jar).
        _sessions[session_cookie] = _sessions[session_cookie] - 5 * 3600

        # Attempt to access a protected endpoint
        resp = auth_client.get("/api/samples")
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════
# T4-22c: All endpoints require auth except /api/health
# ═══════════════════════════════════════════════════════════════════════


class TestAuthEnforcement:
    """T4-22c: All API endpoints return 401 without valid session (except /api/health)."""

    def test_health_exempt_from_auth(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_protected_endpoint_returns_401(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/api/samples")
        assert resp.status_code == 401

    def test_auth_status_exempt(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/api/auth/status")
        assert resp.status_code == 200

    def test_login_endpoint_exempt(self, auth_client: TestClient) -> None:
        resp = auth_client.post("/api/auth/login", json={"password": "wrong"})
        # Should return 401 (wrong password), not blocked by middleware
        assert resp.status_code == 401

    def test_setup_endpoints_exempt(self, auth_client: TestClient) -> None:
        resp = auth_client.get("/api/setup/status")
        assert resp.status_code == 200

    def test_authenticated_request_passes(self, auth_client: TestClient) -> None:
        # Login persists the session cookie on the client instance (the login
        # response's Set-Cookie is captured by the client jar), so subsequent
        # requests are authenticated without a per-request cookies= (#594).
        login_resp = auth_client.post("/api/auth/login", json={"password": "testpin"})
        assert "gi_session" in login_resp.cookies

        # Access protected endpoint — a valid session must succeed, not merely
        # avoid 401. Asserting == 200 (the documented success for /api/samples,
        # see TestAuthDisabled::test_no_auth_needed_when_disabled) also catches a
        # 500 / 403 that `!= 401` would silently pass.
        resp = auth_client.get("/api/samples")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════
# Auth disabled — everything passes through
# ═══════════════════════════════════════════════════════════════════════


class TestAuthDisabled:
    """When auth is disabled, no authentication is required."""

    def test_no_auth_needed_when_disabled(self, noauth_client: TestClient) -> None:
        resp = noauth_client.get("/api/samples")
        # Should not be 401
        assert resp.status_code != 401

    def test_auth_status_shows_disabled(self, noauth_client: TestClient) -> None:
        resp = noauth_client.get("/api/auth/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["auth_enabled"] is False
        assert body["authenticated"] is True  # Everyone is "authenticated" when disabled


# ═══════════════════════════════════════════════════════════════════════
# Logout
# ═══════════════════════════════════════════════════════════════════════


class TestLogout:
    """Test logout destroys session."""

    def test_logout_clears_session(self, auth_client: TestClient) -> None:
        # Login (the session cookie is now on the client jar).
        login_resp = auth_client.post("/api/auth/login", json={"password": "testpin"})
        session_cookie = login_resp.cookies.get("gi_session")

        # Logout — its response deletes the cookie, so the client jar drops it.
        resp = auth_client.post("/api/auth/logout")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # The OLD session value must be rejected server-side: re-set it explicitly
        # (logout cleared it from the jar) so this proves the server destroyed the
        # session, not merely that no cookie was sent (#594).
        auth_client.cookies.set("gi_session", session_cookie)
        resp = auth_client.get("/api/samples")
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════
# Password management
# ═══════════════════════════════════════════════════════════════════════


class TestSetPassword:
    """Test password set/update flows."""

    def test_set_initial_password(self, noauth_client: TestClient, tmp_data_dir: Path) -> None:
        import tomllib

        resp = noauth_client.post(
            "/api/auth/set-password",
            json={"password": "newpin123"},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert "gi_session" in resp.cookies

        cfg = tomllib.loads((tmp_data_dir / "config.toml").read_text(encoding="utf-8"))
        persisted_hash = cfg["yeliztli"]["auth_password_hash"]
        assert cfg["yeliztli"]["auth_enabled"] is True
        assert persisted_hash != "newpin123"
        assert verify_password("newpin123", persisted_hash) is True

    def test_set_password_too_short(self, noauth_client: TestClient) -> None:
        resp = noauth_client.post(
            "/api/auth/set-password",
            json={"password": "ab"},
        )
        assert resp.status_code == 422

    def test_change_password_requires_current(self, auth_client: TestClient) -> None:
        # Login (the session cookie is now on the client jar).
        login_resp = auth_client.post("/api/auth/login", json={"password": "testpin"})
        assert "gi_session" in login_resp.cookies

        # Try to change without current password
        resp = auth_client.post(
            "/api/auth/set-password",
            json={"password": "newpin"},
        )
        assert resp.status_code == 400

    def test_login_no_password_set_returns_400(self, noauth_client: TestClient) -> None:
        resp = noauth_client.post("/api/auth/login", json={"password": "anything"})
        assert resp.status_code == 400

    def test_set_password_requires_auth_when_password_exists(
        self, auth_client: TestClient
    ) -> None:
        """set-password is NOT exempt from auth when a password is already set."""
        resp = auth_client.post(
            "/api/auth/set-password",
            json={"password": "newpin", "current_password": "testpin"},
        )
        # Should be blocked by middleware (no session cookie)
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════
# Remove password (disable auth) — issue #530
# ═══════════════════════════════════════════════════════════════════════


class TestRemovePassword:
    """`POST /api/auth/remove-password` verifies the current password and then
    disables all authentication. Mirrors the `set-password` verify-current gate
    (`test_change_password_requires_current`), which was previously the only
    half of the security-twin pair under test.

    Note: in these fixtures `get_settings` is patched to a fixed object, so the
    handler's `_persist_auth_settings` write does not change the *runtime* auth
    state (a later `/auth/status` would still read the patched `auth_enabled`).
    These tests therefore assert the real, observable side effects — the
    persisted `config.toml` and the in-memory session store — rather than a
    settings re-read.
    """

    def test_remove_password_when_none_set_returns_400(self, noauth_client: TestClient) -> None:
        # No password configured (auth disabled) → middleware passes through and
        # the handler's "nothing to remove" branch returns 400.
        resp = noauth_client.post("/api/auth/remove-password", json={"password": "anything"})
        assert resp.status_code == 400

    def test_remove_password_requires_session_when_password_set(
        self, auth_client: TestClient
    ) -> None:
        """Without a valid session the middleware blocks the request (401) —
        remove-password is NOT in the auth-exempt set, so it can never disable
        auth anonymously."""
        resp = auth_client.post("/api/auth/remove-password", json={"password": "testpin"})
        assert resp.status_code == 401

    def test_remove_password_wrong_password_returns_401_and_keeps_auth(
        self, auth_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """With a valid session but the WRONG current password, the handler's
        verify gate rejects (401) and auth is left untouched — no config is
        persisted to disable it and the live session is not cleared."""
        from backend.auth import _get_session_count

        login = auth_client.post("/api/auth/login", json={"password": "testpin"})
        assert "gi_session" in login.cookies  # session cookie now on the client jar
        assert _get_session_count() == 1

        resp = auth_client.post("/api/auth/remove-password", json={"password": "wrongpin"})
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Password is incorrect."
        # The auth-disabling side effects must NOT have run: the handler raised
        # before `_persist_auth_settings`/`clear_all_sessions`.
        assert not (tmp_data_dir / "config.toml").exists()
        assert _get_session_count() == 1

    def test_remove_password_correct_disables_auth_and_clears_sessions(
        self, auth_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """With a valid session and the correct current password: 200, all
        sessions cleared, and `config.toml` persisted with auth disabled and
        the password hash blanked."""
        import tomllib

        from backend.auth import _get_session_count

        login = auth_client.post("/api/auth/login", json={"password": "testpin"})
        assert "gi_session" in login.cookies  # session cookie now on the client jar
        assert _get_session_count() == 1

        resp = auth_client.post("/api/auth/remove-password", json={"password": "testpin"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        # All sessions invalidated (clear_all_sessions on the success path).
        assert _get_session_count() == 0
        # Persisted: auth disabled + hash blanked in the [yeliztli] table.
        cfg = tomllib.loads((tmp_data_dir / "config.toml").read_text(encoding="utf-8"))
        assert cfg["yeliztli"]["auth_enabled"] is False
        assert cfg["yeliztli"]["auth_password_hash"] == ""


# ═══════════════════════════════════════════════════════════════════════
# Rate limiting
# ═══════════════════════════════════════════════════════════════════════


class TestRateLimiting:
    """Test login rate limiting."""

    def setup_method(self) -> None:
        clear_all_rate_limits()

    def test_rate_limit_after_max_attempts(self, auth_client: TestClient) -> None:
        # Make 5 failed attempts
        for _ in range(5):
            auth_client.post("/api/auth/login", json={"password": "wrong"})

        # 6th attempt should be rate-limited
        resp = auth_client.post("/api/auth/login", json={"password": "wrong"})
        assert resp.status_code == 429
        assert "Too many failed attempts" in resp.json()["detail"]

    def test_successful_login_resets_rate_limit(self, auth_client: TestClient) -> None:
        # Make some failed attempts
        for _ in range(3):
            auth_client.post("/api/auth/login", json={"password": "wrong"})

        # Successful login should reset the counter
        resp = auth_client.post("/api/auth/login", json={"password": "testpin"})
        assert resp.status_code == 200

        # Should be able to make failed attempts again without hitting rate limit
        for _ in range(3):
            resp = auth_client.post("/api/auth/login", json={"password": "wrong"})
            assert resp.status_code == 401

    def teardown_method(self) -> None:
        clear_all_rate_limits()
