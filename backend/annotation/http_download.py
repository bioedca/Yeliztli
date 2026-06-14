"""Resilient HTTP streaming download with retry + Range resume.

Large database downloads (dbNSFP ~47 GB, gnomAD, ClinVar, dbSNP, …) are long
enough that a transient TCP reset or an upstream closing the connection
mid-body is a *when*, not an *if*.  The naive ``client.stream`` loops that
used to live in each ``backend.annotation.*`` module had no recovery: a single
``httpx.RemoteProtocolError`` ("peer closed connection without sending complete
message body") threw away the whole partial transfer and failed the build.

:func:`stream_download` hardens that path while leaving the happy path
untouched:

* **Byte-exact streaming.** Requests are sent with ``Accept-Encoding: identity``
  and bytes are pulled with :meth:`httpx.Response.iter_raw`, so on-wire byte
  offsets match the file on disk and ``Range`` resumption is exact (no
  transparent gzip re-framing).
* **Resume, don't restart.** On a transient failure the partial ``.tmp`` is
  kept and the next attempt issues ``Range: bytes=<offset>-`` (guarded by
  ``If-Range`` against the first response's validator, so a rotated upstream
  artifact triggers a clean full restart instead of a corrupt splice).
* **Progress-aware retry budget.** The retry budget counts *consecutive
  attempts that made no forward progress*.  Any attempt that appends even one
  byte resets it, so a 47 GB transfer that drops every few GB still completes,
  while a genuinely stuck endpoint fails fast.
* **Completeness check.** A body that ends cleanly but short of the advertised
  ``Content-Length`` / ``Content-Range`` total is treated as a retryable
  failure rather than a silent truncation.

The happy path is a single streamed connection at the same chunk size as
before, so there is no throughput regression; retries and the size check only
cost anything when a transfer would otherwise have failed outright.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import structlog

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

logger = structlog.get_logger(__name__)

# Default streaming chunk size (matches the legacy per-DB loops).
DEFAULT_CHUNK_SIZE = 65_536  # 64 KiB

# Default timeouts. ``total`` is generous because these are multi-GB files;
# ``connect`` / ``read`` are tight enough to detect a dead socket quickly so a
# stall becomes a retryable ReadTimeout instead of hanging for an hour.
DEFAULT_TOTAL_TIMEOUT = 3600.0
DEFAULT_CONNECT_TIMEOUT = 30.0
DEFAULT_READ_TIMEOUT = 120.0

# Consecutive *no-progress* attempts tolerated before giving up.
DEFAULT_MAX_RETRIES = 5

# Absolute ceiling on attempts, independent of progress, to bound pathological
# servers that dribble a few bytes per connection and then drop.
DEFAULT_MAX_ATTEMPTS = 200

# Exponential backoff parameters (seconds).
DEFAULT_BACKOFF_BASE = 1.0
DEFAULT_BACKOFF_MAX = 30.0

# httpx transport-level errors worth retrying with a resume.  Deliberately
# excludes ``httpx.LocalProtocolError`` / ``httpx.UnsupportedProtocol`` /
# ``httpx.ProxyError`` (client/config faults that won't fix themselves) and
# ``httpx.HTTPStatusError`` (handled separately by status code).
RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    httpx.RemoteProtocolError,  # peer closed connection mid-body
    httpx.NetworkError,  # ConnectError, ReadError, WriteError, CloseError
    httpx.TimeoutException,  # ConnectTimeout, ReadTimeout, WriteTimeout, PoolTimeout
)

# HTTP status codes that warrant a retry (transient server-side conditions).
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({408, 425, 429, 500, 502, 503, 504})


class DownloadError(Exception):
    """Raised when a download fails after exhausting the retry budget."""


class IncompleteDownloadError(DownloadError):
    """Raised when the received body is shorter than the advertised total."""


class _RetryableStatusError(Exception):
    """Internal: a response status code that should be retried (not surfaced)."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"retryable HTTP status {status_code}")
        self.status_code = status_code


# Exceptions that trigger a backoff-and-resume retry (flattened for ``except``).
_RETRY_TRIGGERS: tuple[type[BaseException], ...] = (
    *RETRYABLE_EXCEPTIONS,
    _RetryableStatusError,
    IncompleteDownloadError,
)


@dataclass
class DownloadOutcome:
    """Result of a successful :func:`stream_download`."""

    path: Path
    total_bytes: int
    """Final number of bytes on disk."""
    expected_total: int | None
    """Advertised total (Content-Length / Content-Range), if the server sent one."""
    headers: Mapping[str, str] = field(default_factory=dict)
    """Headers from the first response (e.g. for ``Last-Modified`` capture)."""
    attempts: int = 1
    """Number of HTTP attempts made (>1 means at least one resume happened)."""
    resumed: bool = False
    """Whether any ``Range`` resume / restart occurred."""
    validator: str | None = None
    """The ETag / Last-Modified the transfer settled on (for a durable ``If-Range``
    on a later cross-process resume). On a 200 restart this is the *new* resource's
    validator, so persisting it keeps subsequent resumes honest about rotations."""


def compute_backoff(
    attempt: int,
    *,
    base: float = DEFAULT_BACKOFF_BASE,
    maximum: float = DEFAULT_BACKOFF_MAX,
) -> float:
    """Exponential backoff with full jitter for retry attempt ``attempt`` (1-based)."""
    ceiling = min(maximum, base * (2 ** max(0, attempt - 1)))
    return random.uniform(0.0, ceiling)  # noqa: S311 (jitter, not crypto)


def _content_range_total(response: httpx.Response) -> int | None:
    """Parse the *full* size from a ``Content-Range`` header (``bytes 100-999/5000``).

    Returns the ``/total`` component, or ``None`` when the total is unknown
    (``bytes 100-999/*``) or the header is absent.  Deliberately does **not**
    fall back to ``Content-Length``: this is only called on 206/416 responses,
    where ``Content-Length`` is the length of the returned *range*, not the size
    of the whole file — using it as the total would corrupt the completeness check.
    """
    content_range = response.headers.get("Content-Range", "")
    if "/" in content_range:
        total_str = content_range.rsplit("/", 1)[1].strip()
        if total_str and total_str != "*":
            try:
                return int(total_str)
            except ValueError:
                return None
    return None


def _validator(response: httpx.Response) -> str | None:
    """Return a strong-ish ``If-Range`` validator (ETag preferred, else Last-Modified)."""
    return response.headers.get("ETag") or response.headers.get("Last-Modified")


def stream_download(
    url: str,
    tmp_path: Path,
    *,
    progress_callback: Callable[[int, int | None], None] | None = None,
    on_chunk: Callable[[int], None] | None = None,
    timeout: float = DEFAULT_TOTAL_TIMEOUT,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    read_timeout: float = DEFAULT_READ_TIMEOUT,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_retries: int = DEFAULT_MAX_RETRIES,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    extra_headers: Mapping[str, str] | None = None,
    resumable: bool = False,
    validator: str | None = None,
    on_validator: Callable[[str], None] | None = None,
    client_factory: Callable[[], httpx.Client] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> DownloadOutcome:
    """Stream ``url`` to ``tmp_path`` with retry + ``Range`` resume.

    By default the download starts fresh: any pre-existing ``tmp_path`` is
    removed first, so there is no risk of splicing onto a stale partial from an
    earlier (possibly different) URL, and the partial is also removed on
    permanent failure.  Resume happens *within* this call — partial progress
    survives transient failures and is continued via ``Range``.

    When ``resumable=True`` (used by :class:`~backend.db.download_manager.
    DownloadManager`, which tracks the URL↔partial mapping itself), a
    pre-existing ``tmp_path`` is continued via ``Range`` and the partial is
    *kept* on permanent failure so a later call can resume it.

    Args:
        url: Remote URL to download.
        tmp_path: Destination temp file.  The caller performs the atomic rename
            to the final path on success.
        progress_callback: Called with ``(file_offset, total_bytes|None)`` after
            each chunk, where ``file_offset`` is the current size of the partial
            file.  It increases monotonically during normal streaming and resumes,
            but resets to 0 and climbs again if the server forces a full restart
            (ignored ``Range`` / rotated resource) — i.e. it always reflects the
            true bytes-on-disk, which is what resume and checkpointing rely on.
        on_chunk: Optional lighter hook called with ``cumulative_bytes`` after
            each chunk (used by :class:`DownloadManager` for DB checkpointing).
        timeout: Total per-request timeout (seconds).
        connect_timeout: Connect timeout (seconds).
        read_timeout: Per-read timeout (seconds) — bounds a stalled socket.
        chunk_size: Streaming chunk size (bytes).
        max_retries: Consecutive no-progress attempts tolerated before failing.
        max_attempts: Absolute attempt ceiling regardless of progress.
        extra_headers: Extra request headers (merged; ``Range`` / ``If-Range`` /
            ``Accept-Encoding`` are managed internally).
        validator: A previously-captured ``If-Range`` validator (ETag /
            Last-Modified) to seed a cross-process resume, so the first ``Range``
            request is guarded against an upstream that rotated since the partial
            was written. Returned (possibly re-captured) on the outcome.
        on_validator: Called with the validator the moment it is first captured
            (or re-captured after a 200 restart), so the caller can persist it
            mid-transfer — an interrupted download that never returns is exactly
            the case a later cross-process resume needs the validator for.
        client_factory: Optional factory returning an ``httpx.Client`` (for
            tests / custom transports).  Defaults to a sensible client.
        sleep: Injectable sleep (tests pass a no-op to avoid real backoff waits).

    Returns:
        :class:`DownloadOutcome` describing the completed transfer.

    Raises:
        DownloadError: On permanent failure (retry budget exhausted).  The
            partial ``tmp_path`` is removed before raising.
        httpx.HTTPStatusError: On a non-retryable HTTP status (e.g. 404).
    """
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    if not resumable:
        # Start fresh — never resume onto a leftover partial we can't vouch for.
        tmp_path.unlink(missing_ok=True)

    def _make_client() -> httpx.Client:
        if client_factory is not None:
            return client_factory()
        return httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout, connect=connect_timeout, read=read_timeout),
        )

    expected_total: int | None = None
    first_headers: Mapping[str, str] | None = None
    # ``validator`` is seeded from the param: on a cross-process resume the caller
    # passes the ETag/Last-Modified it persisted, so the FIRST Range request can
    # carry ``If-Range`` and the server forces a clean 200 restart if the upstream
    # rotated (instead of splicing new bytes onto a stale partial). A 200 below
    # re-captures the current validator; a 206 only learns one if still unset.
    no_progress_failures = 0
    attempt = 0
    resumed = False

    try:
        while True:
            attempt += 1
            offset = tmp_path.stat().st_size if tmp_path.exists() else 0
            # File size at the start of this attempt — used to judge real forward
            # progress even when a 200 restart resets ``offset`` to 0 below.
            attempt_start_offset = offset

            req_headers: dict[str, str] = {"Accept-Encoding": "identity"}
            if extra_headers:
                req_headers.update(extra_headers)
            if offset > 0:
                req_headers["Range"] = f"bytes={offset}-"
                if validator:
                    req_headers["If-Range"] = validator

            try:
                with (
                    _make_client() as client,
                    client.stream("GET", url, headers=req_headers) as response,
                ):
                    status = response.status_code

                    # ── Decide append vs. fresh, and learn total/validator ──
                    if offset > 0 and status == 416:
                        # Range Not Satisfiable. If we hold the whole file, done.
                        total = _content_range_total(response)
                        if total is not None and offset >= total:
                            if first_headers is None:
                                # httpx.Headers is case-insensitive; keep it as-is
                                # so callers' .get("Last-Modified") works.
                                first_headers = response.headers
                            return DownloadOutcome(
                                path=tmp_path,
                                total_bytes=offset,
                                expected_total=total,
                                headers=first_headers,
                                attempts=attempt,
                                resumed=True,
                                validator=validator,
                            )
                        # Bogus/oversized partial — truncate, restart next attempt.
                        tmp_path.unlink(missing_ok=True)
                        raise _RetryableStatusError(416)

                    if offset > 0 and status == 206:
                        mode = "ab"
                        total = _content_range_total(response)
                        # On a 206 the Content-Range total covers the whole file.
                        if total is not None:
                            expected_total = total
                        resumed = True
                        # Continuing the same resource (a cross-call resume may
                        # begin on a 206): capture validator/headers once, before
                        # streaming, so a mid-body failure still leaves an If-Range
                        # validator for the next attempt. httpx.Headers is
                        # case-insensitive and stays valid after the stream closes.
                        if first_headers is None:
                            first_headers = response.headers
                        if validator is None:
                            validator = _validator(response)
                            if on_validator is not None and validator is not None:
                                on_validator(validator)
                    elif status == 200:
                        # Fresh body, or server ignored Range (resource changed /
                        # no Range support) — restart from scratch.
                        if offset > 0:
                            resumed = True
                            tmp_path.unlink(missing_ok=True)
                            offset = 0
                        mode = "wb"
                        content_length = response.headers.get("Content-Length")
                        expected_total = int(content_length) if content_length else None
                        # A 200 is a full (re)download of the *current* resource, so
                        # (re)capture its validator + headers. Re-capturing matters:
                        # if a prior validator no longer matches (upstream rotated),
                        # we must adopt the new version's validator or every later
                        # resume would mismatch and force yet another full restart.
                        first_headers = response.headers
                        new_validator = _validator(response)
                        if (
                            on_validator is not None
                            and new_validator is not None
                            and new_validator != validator
                        ):
                            on_validator(new_validator)
                        validator = new_validator
                    elif status in RETRYABLE_STATUS_CODES:
                        raise _RetryableStatusError(status)
                    else:
                        # Non-retryable status (e.g. 404) — raise HTTPStatusError.
                        response.raise_for_status()
                        raise DownloadError(f"unexpected HTTP status {status} for {url}")

                    # ── Stream the body ──
                    written = offset if mode == "ab" else 0
                    with open(tmp_path, mode) as f:
                        for chunk in response.iter_raw(chunk_size):
                            f.write(chunk)
                            written += len(chunk)
                            if progress_callback is not None:
                                progress_callback(written, expected_total)
                            if on_chunk is not None:
                                on_chunk(written)

                # ── Stream ended cleanly — verify completeness ──
                final_size = tmp_path.stat().st_size if tmp_path.exists() else 0
                if expected_total is not None and final_size < expected_total:
                    raise IncompleteDownloadError(
                        f"received {final_size:,} of {expected_total:,} bytes from {url}"
                    )

                return DownloadOutcome(
                    path=tmp_path,
                    total_bytes=final_size,
                    expected_total=expected_total,
                    headers=first_headers,
                    attempts=attempt,
                    resumed=resumed or attempt > 1,
                    validator=validator,
                )

            except _RETRY_TRIGGERS as exc:
                new_offset = tmp_path.stat().st_size if tmp_path.exists() else 0
                # Real progress = the file grew beyond where this attempt started.
                # (A 200 restart that re-fetches the same prefix is NOT progress,
                # so a Range-ignoring server that keeps dropping fails fast.)
                made_progress = new_offset > attempt_start_offset
                no_progress_failures = 0 if made_progress else no_progress_failures + 1
                resumed = True

                attempts_exhausted = attempt >= max_attempts
                budget_exhausted = no_progress_failures > max_retries
                if budget_exhausted or attempts_exhausted:
                    reason = "max_attempts" if attempts_exhausted else "max_retries"
                    raise DownloadError(
                        f"download failed after {attempt} attempt(s) "
                        f"({new_offset:,} bytes; {reason}): "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc

                delay = compute_backoff(no_progress_failures or 1)
                logger.warning(
                    "download_retry",
                    url=url,
                    attempt=attempt,
                    offset=new_offset,
                    made_progress=made_progress,
                    retry_in_s=round(delay, 2),
                    error=f"{type(exc).__name__}: {exc}",
                )
                sleep(delay)
    except BaseException:
        if not resumable:
            # No cross-call resume: never leave a partial behind on failure.
            # Guard the cleanup so a unlink error (e.g. read-only dir) can't mask
            # the original download exception.
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError as cleanup_err:
                logger.warning(
                    "download_cleanup_failed", path=str(tmp_path), error=str(cleanup_err)
                )
        raise
