"""User preferences API (P4-26a).

Endpoints for persisting UI preferences (theme) to config.toml.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from backend.config import (
    config_toml_path,
    config_write_lock,
    get_settings,
    read_config_section,
    write_config_section,
    write_config_toml,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/preferences", tags=["preferences"])


# ── TOML helpers (reuse pattern from setup.py) ─────────────────────


def _read_config_toml(config_path: Path) -> dict[str, dict[str, object]]:
    """Read and parse config.toml, returning empty dict on missing or invalid file."""
    if not config_path.exists():
        return {}
    try:
        import tomllib

        return tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        logger.warning(
            "config_toml_parse_failed",
            extra={"path": str(config_path), "error": str(exc)},
        )
        return {}


# ── Models ──────────────────────────────────────────────────────────


class ThemeResponse(BaseModel):
    theme: Literal["light", "dark", "system"]


class ThemeRequest(BaseModel):
    theme: Literal["light", "dark", "system"]


UpdateCheckInterval = Literal["off", "startup", "daily", "weekly"]


class UpdateCheckIntervalResponse(BaseModel):
    update_check_interval: UpdateCheckInterval


class UpdateCheckIntervalRequest(BaseModel):
    update_check_interval: UpdateCheckInterval


# ── Endpoints ───────────────────────────────────────────────────────


@router.get("/theme", response_model=ThemeResponse)
async def get_theme() -> ThemeResponse:
    """Return the current theme preference from settings."""
    settings = get_settings()
    return ThemeResponse(theme=settings.theme)


@router.put("/theme", response_model=ThemeResponse)
async def set_theme(body: ThemeRequest) -> ThemeResponse:
    """Update theme preference and persist to config.toml."""
    # The single config.toml the Settings read source loads (home dir). Writing
    # anywhere else (e.g. a relocated data_dir) would never round-trip back.
    config_path = config_toml_path()

    with config_write_lock:
        content = _read_config_toml(config_path)

        section = read_config_section(content)
        section["theme"] = body.theme
        write_config_section(content, section)

        write_config_toml(config_path, content)

    # Clear cached settings so next read picks up the new value
    get_settings.cache_clear()

    logger.info("theme_updated", extra={"theme": body.theme})
    return ThemeResponse(theme=body.theme)


@router.get("/update-check-interval", response_model=UpdateCheckIntervalResponse)
async def get_update_check_interval() -> UpdateCheckIntervalResponse:
    """Return the current automatic-update-check interval from settings."""
    settings = get_settings()
    return UpdateCheckIntervalResponse(update_check_interval=settings.update_check_interval)


@router.put("/update-check-interval", response_model=UpdateCheckIntervalResponse)
async def set_update_check_interval(
    body: UpdateCheckIntervalRequest,
) -> UpdateCheckIntervalResponse:
    """Update the automatic-update-check interval and persist it to config.toml.

    ``"off"`` stops the automatic outbound update/version checks (the periodic
    task no-ops and ``/api/updates/check`` / ``/app-update`` return without any
    outbound request — see ``update_check_interval`` in config.py and #1285).
    Surfaced so the Settings UI can offer an "Automatically check for updates"
    toggle without editing config.toml by hand (#1287).
    """
    config_path = config_toml_path()

    with config_write_lock:
        content = _read_config_toml(config_path)

        section = read_config_section(content)
        section["update_check_interval"] = body.update_check_interval
        write_config_section(content, section)

        write_config_toml(config_path, content)

    # Clear cached settings so next read picks up the new value
    get_settings.cache_clear()

    logger.info(
        "update_check_interval_updated",
        extra={"update_check_interval": body.update_check_interval},
    )
    return UpdateCheckIntervalResponse(update_check_interval=body.update_check_interval)
