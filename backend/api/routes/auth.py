"""Authentication API routes (P4-21a).

Endpoints:
    GET  /api/auth/status        — Check auth state (enabled, has session)
    POST /api/auth/login         — Login with PIN/password
    POST /api/auth/logout        — Logout (destroy session)
    POST /api/auth/set-password  — Set or update the password
    POST /api/auth/remove-password — Remove password (disable auth)
"""

from __future__ import annotations

from pathlib import Path

import structlog
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend.auth import (
    check_rate_limit,
    clear_all_sessions,
    create_session,
    destroy_session,
    hash_password,
    record_failed_attempt,
    reset_rate_limit,
    validate_session,
    verify_password,
)
from backend.config import (
    get_settings,
    read_config_section,
    write_config_section,
    write_config_toml,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# ── Response / request models ─────────────────────────────────────────


class AuthStatusResponse(BaseModel):
    """Current authentication state."""

    auth_enabled: bool
    has_password: bool
    authenticated: bool


class LoginRequest(BaseModel):
    """Login request body."""

    password: str = Field(..., min_length=1)


class LoginResponse(BaseModel):
    """Login result."""

    success: bool
    message: str


class SetPasswordRequest(BaseModel):
    """Set/update password request."""

    password: str = Field(..., min_length=4, max_length=72)
    current_password: str = ""


class SetPasswordResponse(BaseModel):
    """Password set result."""

    success: bool
    message: str


class RemovePasswordResponse(BaseModel):
    """Password removal result."""

    success: bool
    message: str


# ── Config persistence ────────────────────────────────────────────────


def _read_config_toml(config_path: Path) -> dict:
    """Read config.toml, returning empty dict on missing/invalid."""
    if not config_path.exists():
        return {}
    try:
        import tomllib

        return tomllib.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _persist_auth_settings(*, auth_enabled: bool, auth_password_hash: str) -> None:
    """Write auth settings to config.toml and bust the settings cache."""
    settings = get_settings()
    config_path = settings.data_dir / "config.toml"
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    existing = _read_config_toml(config_path)
    section = read_config_section(existing)
    section["auth_enabled"] = auth_enabled
    section["auth_password_hash"] = auth_password_hash
    write_config_section(existing, section)
    write_config_toml(config_path, existing)

    # Bust the lru_cache so new settings take effect
    get_settings.cache_clear()


# ── GET /api/auth/status ──────────────────────────────────────────────


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status(request: Request) -> AuthStatusResponse:
    """Check current auth state.

    Returns whether auth is enabled, whether a password is set,
    and whether the current request has a valid session.
    """
    settings = get_settings()
    has_password = bool(settings.auth_password_hash)
    enabled = settings.auth_enabled and has_password

    # Check if currently authenticated
    authenticated = False
    if not enabled:
        # Auth disabled means everyone is "authenticated"
        authenticated = True
    else:
        session_id = request.cookies.get("gi_session")
        if session_id:
            authenticated = validate_session(session_id, settings.session_timeout_hours)

    return AuthStatusResponse(
        auth_enabled=enabled,
        has_password=has_password,
        authenticated=authenticated,
    )


# ── POST /api/auth/login ─────────────────────────────────────────────


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request, response: Response) -> LoginResponse:
    """Authenticate with PIN/password and set session cookie."""
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"

    # Rate limit check
    rate_msg = check_rate_limit(client_ip)
    if rate_msg:
        raise HTTPException(status_code=429, detail=rate_msg)

    if not settings.auth_password_hash:
        raise HTTPException(
            status_code=400,
            detail="No password set. Authentication is not configured.",
        )

    if not verify_password(body.password, settings.auth_password_hash):
        record_failed_attempt(client_ip)
        logger.warning("auth_login_failed", client_ip=client_ip)
        raise HTTPException(status_code=401, detail="Invalid password")

    reset_rate_limit(client_ip)
    session_id = create_session()
    response.set_cookie(
        key="gi_session",
        value=session_id,
        httponly=True,
        samesite="lax",
        max_age=settings.session_timeout_hours * 3600,
        path="/",
    )

    logger.info("auth_login_success")
    return LoginResponse(success=True, message="Login successful")


# ── POST /api/auth/logout ────────────────────────────────────────────


@router.post("/logout", response_model=LoginResponse)
async def logout(request: Request, response: Response) -> LoginResponse:
    """Destroy the current session and clear the cookie."""
    session_id = request.cookies.get("gi_session")
    if session_id:
        destroy_session(session_id)

    response.delete_cookie(key="gi_session", path="/")
    logger.info("auth_logout")
    return LoginResponse(success=True, message="Logged out successfully")


# ── POST /api/auth/set-password ───────────────────────────────────────


@router.post("/set-password", response_model=SetPasswordResponse)
async def set_password(
    body: SetPasswordRequest, request: Request, response: Response
) -> SetPasswordResponse:
    """Set or update the authentication password.

    If a password is already set, the current_password must be provided
    and must be correct. The new password is bcrypt-hashed and stored
    in config.toml. Auth is enabled, and all existing sessions are
    invalidated.
    """
    settings = get_settings()

    # If password already set, require current password
    if settings.auth_password_hash:
        if not body.current_password:
            raise HTTPException(
                status_code=400,
                detail="Current password required to change password.",
            )
        if not verify_password(body.current_password, settings.auth_password_hash):
            raise HTTPException(status_code=401, detail="Current password is incorrect.")

    # Hash and persist
    new_hash = hash_password(body.password)
    _persist_auth_settings(auth_enabled=True, auth_password_hash=new_hash)

    # Invalidate all existing sessions
    clear_all_sessions()

    # Create a new session for the user who just set the password
    session_id = create_session()
    response.set_cookie(
        key="gi_session",
        value=session_id,
        httponly=True,
        samesite="lax",
        max_age=settings.session_timeout_hours * 3600,
        path="/",
    )

    logger.info("auth_password_set")
    return SetPasswordResponse(
        success=True, message="Password set successfully. Authentication enabled."
    )


# ── POST /api/auth/remove-password ────────────────────────────────────


@router.post("/remove-password", response_model=RemovePasswordResponse)
async def remove_password(body: LoginRequest, response: Response) -> RemovePasswordResponse:
    """Remove the password and disable authentication.

    Requires the current password for verification.
    """
    settings = get_settings()

    if not settings.auth_password_hash:
        raise HTTPException(status_code=400, detail="No password is currently set.")

    if not verify_password(body.password, settings.auth_password_hash):
        raise HTTPException(status_code=401, detail="Password is incorrect.")

    _persist_auth_settings(auth_enabled=False, auth_password_hash="")
    clear_all_sessions()
    response.delete_cookie(key="gi_session", path="/")

    logger.info("auth_password_removed")
    return RemovePasswordResponse(
        success=True,
        message="Password removed. Authentication disabled.",
    )
