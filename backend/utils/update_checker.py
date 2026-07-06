"""App update checker — GitHub Releases API (P4-21d).

On startup, checks the GitHub Releases API for a newer Yeliztli version.
Compares semantic versions. No auto-update — users upgrade via pip.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from packaging.version import InvalidVersion, Version

from backend.main import VERSION

logger = logging.getLogger(__name__)

GITHUB_REPO = "bioedca/Yeliztli"
RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
RELEASES_PER_PAGE = 100
REQUEST_TIMEOUT = 10.0  # seconds
USER_AGENT = f"Yeliztli/{VERSION}"


@dataclass
class AppUpdateInfo:
    """Result of an app update check."""

    update_available: bool
    current_version: str
    latest_version: str | None = None
    release_url: str | None = None
    release_notes: str | None = None
    error: str | None = None


def parse_version(version_str: str) -> Version | None:
    """Parse a version string, stripping leading 'v' if present."""
    cleaned = version_str.lstrip("v")
    try:
        return Version(cleaned)
    except InvalidVersion:
        return None


def _parse_app_release_tag(tag: str) -> Version | None:
    """Return the app version encoded by an app release tag, if any."""
    if tag.startswith("app-v"):
        return parse_version(tag.removeprefix("app-v"))
    if tag.startswith("v"):
        return parse_version(tag.removeprefix("v"))
    return None


def _select_latest_app_release(releases: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Select the highest stable Yeliztli app release from GitHub releases."""
    latest: tuple[Version, dict[str, Any]] | None = None
    for release in releases:
        tag = str(release.get("tag_name") or "")
        version = _parse_app_release_tag(tag)
        if (
            version is None
            or version.is_prerelease
            or release.get("prerelease")
            or release.get("draft")
        ):
            continue
        if latest is None or version > latest[0]:
            latest = (version, release)
    return latest[1] if latest else None


async def check_app_update(current_version: str | None = None) -> AppUpdateInfo:
    """Check GitHub Releases for a newer Yeliztli version.

    Args:
        current_version: Override for testing. Defaults to backend.main.VERSION.

    Returns:
        AppUpdateInfo with comparison result.
    """
    current = current_version or VERSION

    try:
        releases: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            page = 1
            while True:
                resp = await client.get(
                    RELEASES_URL,
                    params={"per_page": RELEASES_PER_PAGE, "page": page},
                    headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": USER_AGENT,
                    },
                )

                if resp.status_code == 404:
                    # No releases published yet
                    return AppUpdateInfo(
                        update_available=False,
                        current_version=current,
                        error="No releases found",
                    )

                if resp.status_code == 403:
                    return AppUpdateInfo(
                        update_available=False,
                        current_version=current,
                        error="GitHub API rate limit exceeded",
                    )

                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, list):
                    return AppUpdateInfo(
                        update_available=False,
                        current_version=current,
                        error="Unexpected GitHub releases response",
                    )

                releases.extend(item for item in data if isinstance(item, dict))
                if len(data) < RELEASES_PER_PAGE:
                    break
                page += 1

        latest_app_release = _select_latest_app_release(releases)
        if latest_app_release is None:
            return AppUpdateInfo(
                update_available=False,
                current_version=current,
            )

        return _compare_versions(current, latest_app_release)

    except httpx.TimeoutException:
        logger.warning("App update check timed out")
        return AppUpdateInfo(
            update_available=False,
            current_version=current,
            error="Request timed out",
        )
    except httpx.HTTPError as exc:
        logger.warning("App update check failed: %s", exc)
        return AppUpdateInfo(
            update_available=False,
            current_version=current,
            error=str(exc),
        )


def _compare_versions(current: str, release_data: dict[str, Any]) -> AppUpdateInfo:
    """Compare current version against a GitHub release response."""
    tag = release_data.get("tag_name", "")
    html_url = release_data.get("html_url")
    body = release_data.get("body", "")

    current_ver = parse_version(current)
    latest_ver = _parse_app_release_tag(tag) or parse_version(tag)

    if current_ver is None or latest_ver is None:
        return AppUpdateInfo(
            update_available=False,
            current_version=current,
            latest_version=tag or None,
            error=f"Could not parse version(s): current={current!r}, latest={tag!r}",
        )

    # Skip pre-releases — only notify for stable versions
    if latest_ver.is_prerelease:
        return AppUpdateInfo(
            update_available=False,
            current_version=current,
            latest_version=str(latest_ver),
            release_url=html_url,
        )

    return AppUpdateInfo(
        update_available=latest_ver > current_ver,
        current_version=current,
        latest_version=str(latest_ver),
        release_url=html_url,
        release_notes=body[:500] if body else None,
    )
