"""Authentication system for Yeliztli (P4-21a).

Optional PIN/password authentication with bcrypt hashing,
session cookies, and 4-hour inactivity timeout.

When auth is disabled (no password set), all requests pass through.
When enabled, all API endpoints require a valid session cookie,
except /api/health which is always exempt.
"""

from __future__ import annotations

import secrets
import time

import bcrypt
import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from backend.config import get_settings

logger = structlog.get_logger(__name__)

# ── Password hashing ─────────────────────────────────────────────────


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash."""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


# ── Session store (in-memory, server-side) ────────────────────────────

# Maps session_id -> last_active timestamp (epoch seconds)
_sessions: dict[str, float] = {}


def create_session() -> str:
    """Create a new session and return the session ID."""
    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = time.time()
    return session_id


def validate_session(session_id: str, timeout_hours: int = 4) -> bool:
    """Check if a session is valid (exists and not expired)."""
    last_active = _sessions.get(session_id)
    if last_active is None:
        return False
    if time.time() - last_active > timeout_hours * 3600:
        # Expired — clean up
        _sessions.pop(session_id, None)
        return False
    # Touch — keep session alive on activity
    _sessions[session_id] = time.time()
    return True


def destroy_session(session_id: str) -> None:
    """Remove a session."""
    _sessions.pop(session_id, None)


def clear_all_sessions() -> None:
    """Remove all sessions (for testing or password change)."""
    _sessions.clear()


def _get_session_count() -> int:
    """Return the number of active sessions (for testing)."""
    return len(_sessions)


def _set_session_timestamp(session_id: str, timestamp: float) -> None:
    """Override the last-active timestamp for a session (for testing)."""
    if session_id in _sessions:
        _sessions[session_id] = timestamp


# ── Rate limiting ─────────────────────────────────────────────────────

# Track failed login attempts per IP: {ip: (fail_count, first_fail_time)}
_login_attempts: dict[str, tuple[int, float]] = {}

_MAX_ATTEMPTS = 5
_LOCKOUT_SECONDS = 300  # 5-minute lockout after max attempts
_ATTEMPT_WINDOW = 600  # 10-minute sliding window


def check_rate_limit(client_ip: str) -> str | None:
    """Check if IP is rate-limited. Returns error message or None."""
    record = _login_attempts.get(client_ip)
    if record is None:
        return None
    count, first_time = record
    elapsed = time.time() - first_time
    # Window expired — reset
    if elapsed > _ATTEMPT_WINDOW:
        _login_attempts.pop(client_ip, None)
        return None
    if count >= _MAX_ATTEMPTS:
        remaining = int(_LOCKOUT_SECONDS - elapsed)
        if remaining > 0:
            return f"Too many failed attempts. Try again in {remaining}s."
        # Lockout expired
        _login_attempts.pop(client_ip, None)
        return None
    return None


def record_failed_attempt(client_ip: str) -> None:
    """Record a failed login attempt for rate limiting."""
    record = _login_attempts.get(client_ip)
    now = time.time()
    if record is None or now - record[1] > _ATTEMPT_WINDOW:
        _login_attempts[client_ip] = (1, now)
    else:
        _login_attempts[client_ip] = (record[0] + 1, record[1])


def reset_rate_limit(client_ip: str) -> None:
    """Clear rate limit on successful login."""
    _login_attempts.pop(client_ip, None)


def clear_all_rate_limits() -> None:
    """Clear all rate limits (for testing)."""
    _login_attempts.clear()


# ── Auth middleware ───────────────────────────────────────────────────

# Paths that are always exempt from auth
_AUTH_EXEMPT_PATHS = frozenset(
    {
        "/api/health",
        "/api/auth/login",
        "/api/auth/status",
    }
)

# Prefixes that are exempt only before auth can be enforced.
_AUTH_EXEMPT_PREFIXES = ("/api/setup",)


def _is_auth_exempt(path: str, *, has_password: bool = False) -> bool:
    """Check if a path is exempt from authentication."""
    if path in _AUTH_EXEMPT_PATHS:
        return True
    # set-password is only exempt when no password is configured yet
    if path == "/api/auth/set-password" and not has_password:
        return True
    for prefix in _AUTH_EXEMPT_PREFIXES:
        if path.startswith(prefix):
            return not has_password
    # Non-API paths (static files, SPA routes) are exempt
    if not path.startswith("/api/"):
        return True
    return False


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware that enforces session-based auth when enabled.

    When auth_enabled is False or no password hash is set, all requests
    pass through. When enabled, non-exempt API requests must have a
    valid session cookie. CORS preflight (OPTIONS) requests always pass.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # CORS preflight must pass through for proper cross-origin handling
        if request.method == "OPTIONS":
            return await call_next(request)

        settings = get_settings()

        # Auth disabled — pass through
        if not settings.auth_enabled or not settings.auth_password_hash:
            return await call_next(request)

        # Exempt paths pass through
        if _is_auth_exempt(request.url.path, has_password=bool(settings.auth_password_hash)):
            return await call_next(request)

        # Check session cookie
        session_id = request.cookies.get("gi_session")
        if not session_id or not validate_session(session_id, settings.session_timeout_hours):
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"},
            )

        return await call_next(request)
