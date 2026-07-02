"""Bundle manifest — single source of truth for bundle URLs / SHA-256 / sizes.

The manifest lives at ``bundles/manifest.json`` in the repo and is fetched at
runtime from raw.githubusercontent.com so users on installed builds can pick
up new bundle releases without a code update. Entries cover both pre-built
bundles (`lai_bundle`, `vep_bundle`, `ancestry_pca`) and pinned upstream URLs
for pipeline DBs (`pipeline_pins`).

For tests and offline development, set the ``YELIZTLI_MANIFEST_PATH``
environment variable to load the manifest from a local file instead of HTTP.

Caching
-------
Successful fetches are cached in-memory for ``CACHE_TTL_SECONDS`` (1 h).
On expiry or first call, a remote fetch is attempted. Failures raise
``ManifestFetchError`` — callers (notably ``backend/api/routes/databases.py``)
fall back to registry defaults when the manifest is unreachable.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import structlog
from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)
_structlog = structlog.get_logger(__name__)

MANIFEST_URL = "https://raw.githubusercontent.com/bioedca/Yeliztli/main/bundles/manifest.json"
CACHE_TTL_SECONDS = 3600.0
# Override env var to load the manifest from a local file (tests / offline dev).
MANIFEST_PATH_ENV = "YELIZTLI_MANIFEST_PATH"
DEFAULT_TIMEOUT = 15.0


class ManifestFetchError(RuntimeError):
    """Raised when the manifest cannot be loaded or parsed."""


@dataclass(frozen=True)
class BundleManifestEntry:
    version: str
    build_date: str
    url: str
    sha256: str
    size_bytes: int
    min_app_version: str | None = None


@dataclass(frozen=True)
class PipelinePinEntry:
    url: str
    last_known_version: str
    # SPDX-ish license identifier of the upstream source (SW-A12 / §9.3); ``None``
    # when not yet recorded. Honored alongside the NOTICE attribution file.
    license: str | None = None


@dataclass(frozen=True)
class Manifest:
    schema_version: int
    generated_at: str
    bundles: dict[str, BundleManifestEntry]
    pipeline_pins: dict[str, PipelinePinEntry]


_cache_lock = threading.Lock()
_cached_manifest: Manifest | None = None
_cached_at: float = 0.0


_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _required_str(entry: dict, key: str, *, context: str) -> str:
    """Return ``entry[key]`` as a string, rejecting missing / None / empty values."""
    val = entry.get(key)
    if val is None or (isinstance(val, str) and not val):
        raise ValueError(f"{context}: field {key!r} must be a non-empty string")
    return str(val)


def _parse_manifest(payload: Any) -> Manifest:
    if not isinstance(payload, dict):
        raise ManifestFetchError(
            f"Manifest payload must be an object, got {type(payload).__name__}"
        )
    try:
        schema_version = int(payload.get("schema_version", 0))
        generated_at = _required_str(payload, "generated_at", context="manifest")
        if "bundles" not in payload or "pipeline_pins" not in payload:
            raise ManifestFetchError("`bundles` and `pipeline_pins` are required")
        bundles_raw = payload["bundles"]
        pins_raw = payload["pipeline_pins"]
        if not isinstance(bundles_raw, dict) or not isinstance(pins_raw, dict):
            raise ManifestFetchError("`bundles` and `pipeline_pins` must be objects")

        bundles = {}
        for name, entry in bundles_raw.items():
            if not isinstance(entry, dict):
                raise ValueError(f"bundle {name!r}: entry must be an object")
            ctx = f"bundle {name!r}"
            sha = _required_str(entry, "sha256", context=ctx)
            if not _SHA256_RE.match(sha):
                raise ValueError(f"{ctx}: sha256 must be 64 hex characters")
            size = int(entry["size_bytes"])
            if size <= 0:
                raise ValueError(f"{ctx}: size_bytes must be > 0")
            min_app_raw = entry.get("min_app_version")
            min_app_version = (
                str(min_app_raw) if isinstance(min_app_raw, str) and min_app_raw else None
            )
            # Only the fields below are extracted; any other key (e.g. a free-text
            # ``comment`` used to document an entry in-place) is intentionally
            # tolerated and ignored.
            bundles[name] = BundleManifestEntry(
                version=_required_str(entry, "version", context=ctx),
                build_date=_required_str(entry, "build_date", context=ctx),
                # url may be empty for bundles delivered out-of-band (e.g. ancestry_pca)
                url=str(entry.get("url", "") or ""),
                sha256=sha.lower(),
                size_bytes=size,
                min_app_version=min_app_version,
            )
        pins = {}
        for name, entry in pins_raw.items():
            if not isinstance(entry, dict):
                raise ValueError(f"pipeline pin {name!r}: entry must be an object")
            ctx = f"pipeline pin {name!r}"
            pins[name] = PipelinePinEntry(
                url=_required_str(entry, "url", context=ctx),
                last_known_version=str(entry.get("last_known_version", "") or ""),
                license=(str(entry["license"]) if entry.get("license") else None),
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise ManifestFetchError(f"Manifest payload malformed: {exc}") from exc

    manifest = Manifest(
        schema_version=schema_version,
        generated_at=generated_at,
        bundles=bundles,
        pipeline_pins=pins,
    )
    _emit_min_app_version_advisories(manifest)
    return manifest


def _current_app_version() -> str:
    """Return the running app's semver string.

    Lazy-imported so this module does not pull in the FastAPI app graph at
    import time (avoids a circular import via ``backend.main``). Overridable
    via monkeypatch in tests.
    """
    from backend.main import VERSION

    return VERSION


def _emit_min_app_version_advisories(manifest: Manifest) -> None:
    """Advisory: emit a structured warning for each bundle whose advisory
    ``min_app_version`` exceeds the running app version.

    Never raises and never refuses to load — the manifest is the contract
    (Plan §2.2, §5.5). Bundles without a ``min_app_version`` are skipped.
    Malformed version strings on either side are logged once and skipped.
    """
    try:
        installed_raw = _current_app_version()
    except Exception:  # pragma: no cover — defensive; main.VERSION is a constant
        return
    try:
        installed = Version(installed_raw.lstrip("v"))
    except InvalidVersion:
        return

    for name, entry in manifest.bundles.items():
        if entry.min_app_version is None:
            continue
        try:
            required = Version(entry.min_app_version.lstrip("v"))
        except InvalidVersion:
            continue
        if installed < required:
            try:
                _structlog.warning(
                    "manifest_min_app_version_below_threshold",
                    bundle=name,
                    installed_app_version=str(installed),
                    required_app_version=str(required),
                )
            except Exception:
                pass


def _load_local(path: Path) -> Manifest:
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestFetchError(f"Failed to read local manifest at {path}: {exc}") from exc
    return _parse_manifest(payload)


def _load_remote(timeout: float) -> Manifest:
    try:
        resp = httpx.get(
            MANIFEST_URL,
            timeout=timeout,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise ManifestFetchError(f"Failed to fetch manifest: {exc}") from exc

    try:
        payload = resp.json()
    except ValueError as exc:
        raise ManifestFetchError(f"Manifest response was not valid JSON: {exc}") from exc

    return _parse_manifest(payload)


def fetch_manifest(
    timeout: float = DEFAULT_TIMEOUT,
    *,
    force_refresh: bool = False,
) -> Manifest:
    """Return the bundle manifest, fetching remotely or from the env override.

    Caching: a successful fetch is cached in-memory for ``CACHE_TTL_SECONDS``.
    Set ``force_refresh=True`` to bypass the cache. Set the
    ``YELIZTLI_MANIFEST_PATH`` env var to load from a local JSON file
    (the env override is never cached so tests can swap files freely).

    Raises ``ManifestFetchError`` if the manifest cannot be loaded or parsed.
    """
    global _cached_manifest, _cached_at

    override = os.environ.get(MANIFEST_PATH_ENV)
    if override:
        return _load_local(Path(override))

    with _cache_lock:
        now = time.monotonic()
        if (
            not force_refresh
            and _cached_manifest is not None
            and (now - _cached_at) < CACHE_TTL_SECONDS
        ):
            return _cached_manifest

        manifest = _load_remote(timeout)
        _cached_manifest = manifest
        _cached_at = now
        return manifest


def get_bundle_info(
    name: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> BundleManifestEntry | None:
    """Return the bundle entry for ``name``, or ``None`` if missing or unfetchable."""
    try:
        manifest = fetch_manifest(timeout=timeout)
    except ManifestFetchError as exc:
        logger.warning("Manifest unavailable for bundle %r: %s", name, exc)
        return None
    return manifest.bundles.get(name)


def get_pipeline_pin(
    name: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> PipelinePinEntry | None:
    """Return the pipeline pin entry for ``name``, or ``None`` if missing or unfetchable."""
    try:
        manifest = fetch_manifest(timeout=timeout)
    except ManifestFetchError as exc:
        logger.warning("Manifest unavailable for pipeline pin %r: %s", name, exc)
        return None
    return manifest.pipeline_pins.get(name)


def reset_cache() -> None:
    """Clear the in-memory cache. Intended for tests."""
    global _cached_manifest, _cached_at
    with _cache_lock:
        _cached_manifest = None
        _cached_at = 0.0
