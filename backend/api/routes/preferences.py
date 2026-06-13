"""User preferences API (P4-26a).

Endpoints for persisting UI preferences (theme) to config.toml.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from backend.config import (
    DEFAULT_DATA_DIR,
    get_settings,
    read_config_section,
    write_config_section,
    write_config_toml,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/preferences", tags=["preferences"])

_config_lock = threading.Lock()


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


# ── Endpoints ───────────────────────────────────────────────────────


@router.get("/theme", response_model=ThemeResponse)
async def get_theme() -> ThemeResponse:
    """Return the current theme preference from settings."""
    settings = get_settings()
    return ThemeResponse(theme=settings.theme)


@router.put("/theme", response_model=ThemeResponse)
async def set_theme(body: ThemeRequest) -> ThemeResponse:
    """Update theme preference and persist to config.toml."""
    config_path = DEFAULT_DATA_DIR / "config.toml"

    with _config_lock:
        content = _read_config_toml(config_path)

        section = read_config_section(content)
        section["theme"] = body.theme
        write_config_section(content, section)

        write_config_toml(config_path, content)

    # Clear cached settings so next read picks up the new value
    get_settings.cache_clear()

    logger.info("theme_updated", extra={"theme": body.theme})
    return ThemeResponse(theme=body.theme)
