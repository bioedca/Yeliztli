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

import contextvars
import hashlib
import hmac
import random
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

import httpx
import structlog

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

logger = structlog.get_logger(__name__)

# Default streaming chunk size (matches the legacy per-DB loops).
DEFAULT_CHUNK_SIZE = 65_536  # 64 KiB

# Default timeouts. NOTE: httpx has **no** total/overall request timeout — only
# ``connect`` / ``read`` / ``write`` / ``pool`` (the first positional arg is just
# the default for any unset one). So ``DEFAULT_TOTAL_TIMEOUT`` only sets the
# ``write`` / ``pool`` defaults; it does NOT bound how long a whole transfer may
# take. ``read`` bounds the gap between chunks — it catches a *fully dead* socket
# (no bytes for ``read`` seconds) but NOT a server that dribbles a few bytes
# under the read timeout indefinitely. The minimum-throughput watchdog below is
# what bounds that throttled-but-alive case (see ``min_throughput_bps``).
DEFAULT_TOTAL_TIMEOUT = 3600.0
DEFAULT_CONNECT_TIMEOUT = 30.0
DEFAULT_READ_TIMEOUT = 120.0

# Minimum-throughput watchdog. A heavily-throttled transfer that keeps delivering
# a trickle (one small chunk every < ``read`` seconds) never trips the read
# timeout and crawls forever — the observed AlphaMissense failure, where the
# transfer effectively stalled and only a SIGTERM stopped it. If sustained
# throughput over a ``DEFAULT_STALL_WINDOW``-second window falls below
# ``DEFAULT_MIN_THROUGHPUT_BPS``, the attempt is aborted and retried (a fresh
# connection often escapes a throttle); a persistent throttle then fails fast
# via the no-progress budget instead of hanging. The floor is deliberately low
# (~1 KiB/s) so a genuinely slow-but-real link is left alone. Pass
# ``min_throughput_bps=None`` to disable.
DEFAULT_MIN_THROUGHPUT_BPS = 1024.0
DEFAULT_STALL_WINDOW = 60.0

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


class DownloadBindingError(DownloadError):
    """A scoped download did not match its approved strong-ETag snapshot."""


@dataclass(frozen=True)
class StrongEtagDownloadExpectation:
    """Expected identity and completeness metadata for one scoped response."""

    etag: str
    resolved_source_hmac: str
    content_length: int
    # Optional release metadata to bind independently of byte identity.  A
    # strong ETag remains the only byte-identity validator; this field prevents
    # a builder from persisting version metadata different from the offer.
    last_modified: str | None = None


@dataclass
class _StrongEtagDownloadScope:
    expectations: dict[str, StrongEtagDownloadExpectation]
    source_binding_key: bytes = field(repr=False)
    completed: set[str] = field(default_factory=set)

    def expectation_for(self, url: str) -> StrongEtagDownloadExpectation:
        try:
            return self.expectations[url]
        except KeyError:
            # Never echo a possibly signed URL from a scoped build.
            raise DownloadBindingError("scoped download requested an unexpected source") from None

    def mark_complete(self, url: str) -> None:
        self.completed.add(url)

    def assert_complete(self) -> None:
        if self.completed != set(self.expectations):
            raise DownloadBindingError(
                "scoped download did not successfully consume every expected source"
            )


_STRONG_ETAG_DOWNLOAD_SCOPE: contextvars.ContextVar[_StrongEtagDownloadScope | None] = (
    contextvars.ContextVar("strong_etag_download_scope", default=None)
)


def is_strong_etag(value: str) -> bool:
    """Return whether *value* is one sendable, syntactically strong entity tag."""
    if not isinstance(value, str) or len(value) < 2 or value.startswith("W/"):
        return False
    if value[0] != '"' or value[-1] != '"':
        return False
    # RFC 9110 also permits obs-text bytes, but httpx string-valued request
    # headers are ASCII encoded.  Reject that optional extension during the
    # sanitized snapshot step instead of failing later while constructing
    # ``If-Match``.
    return all(char == "!" or "#" <= char <= "~" for char in value[1:-1])


def normalized_download_source(url: str) -> str:
    """Return the exact redirect resource path without credentials or parameters."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError
        hostname = parsed.hostname
        host = f"[{hostname}]" if ":" in hostname else hostname
        port = f":{parsed.port}" if parsed.port is not None else ""
    except (TypeError, ValueError):
        raise DownloadBindingError("scoped download has an invalid source identity") from None
    return urlunsplit((parsed.scheme, f"{host}{port}", parsed.path or "/", "", ""))


def download_source_identity(url: str, source_binding_key: bytes) -> str:
    """Return a keyed binding for a response's exact normalized resource.

    The path is required to distinguish resources on hosts that reuse an ETag,
    but a plain digest of a credential-bearing path is an offline guessing
    oracle.  The key is kept outside durable task state and is never retained by
    the returned value.
    """
    if not isinstance(source_binding_key, bytes) or len(source_binding_key) != 32:
        raise DownloadBindingError("scoped download source-binding key is unavailable")
    normalized = normalized_download_source(url)
    return hmac.new(
        source_binding_key,
        b"yeliztli-download-source-v2\0" + normalized.encode(),
        hashlib.sha256,
    ).hexdigest()


@contextmanager
def bind_strong_etag_downloads(
    expectations: Mapping[str, StrongEtagDownloadExpectation],
    *,
    source_binding_key: bytes,
) -> Iterator[_StrongEtagDownloadScope]:
    """Bind all downloads in this context to exact strong ETags and sources.

    The scope is inert for callers that do not opt in.  Within it, every
    :func:`stream_download` URL must be declared, every 200/206 must report the
    expected strong ETag, final redirect origin, and complete-object size,
    and every declared URL must complete successfully before normal exit.
    """
    if not expectations:
        raise DownloadBindingError("scoped download has no expected sources")
    if not isinstance(source_binding_key, bytes) or len(source_binding_key) != 32:
        raise DownloadBindingError("scoped download source-binding key is unavailable")
    checked: dict[str, StrongEtagDownloadExpectation] = {}
    for requested_url, expectation in expectations.items():
        if not is_strong_etag(expectation.etag):
            raise DownloadBindingError("scoped download requires a strong quoted ETag")
        if (
            not isinstance(expectation.content_length, int)
            or isinstance(expectation.content_length, bool)
            or expectation.content_length <= 0
        ):
            raise DownloadBindingError("scoped download requires a positive content length")
        if expectation.last_modified is not None and (
            not isinstance(expectation.last_modified, str)
            or not expectation.last_modified
            or not expectation.last_modified.isascii()
            or any(not 0x20 <= ord(char) <= 0x7E for char in expectation.last_modified)
        ):
            raise DownloadBindingError("scoped download release metadata is malformed")
        source_digest = expectation.resolved_source_hmac
        if (
            not isinstance(source_digest, str)
            or len(source_digest) != 64
            or any(char not in "0123456789abcdef" for char in source_digest)
        ):
            raise DownloadBindingError("scoped download source identity is malformed")
        if requested_url in checked:
            raise DownloadBindingError("scoped download declares a duplicate source")
        checked[requested_url] = expectation

    scope = _StrongEtagDownloadScope(checked, source_binding_key)
    token = _STRONG_ETAG_DOWNLOAD_SCOPE.set(scope)
    try:
        yield scope
        scope.assert_complete()
    finally:
        _STRONG_ETAG_DOWNLOAD_SCOPE.reset(token)


def _bound_response_total(response: httpx.Response, status: int) -> int | None:
    if status == 200:
        value = response.headers.get("Content-Length", "")
        if isinstance(value, str) and value.isascii() and value.isdecimal():
            return int(value)
        return None
    if status == 206:
        return _content_range_total(response)
    return None


def _verify_bound_response(
    response: httpx.Response,
    expectation: StrongEtagDownloadExpectation,
    requested_offset: int,
    source_binding_key: bytes,
) -> None:
    """Reject a content response before any bytes are written if it drifted."""
    status = response.status_code
    if status == 412:
        raise DownloadBindingError("scoped download source changed after approval")
    if status not in {200, 206}:
        return
    response_etag = response.headers.get("ETag", "")
    if not is_strong_etag(response_etag) or response_etag != expectation.etag:
        raise DownloadBindingError("scoped download response ETag does not match approval")
    response_source = download_source_identity(str(response.url), source_binding_key)
    if not hmac.compare_digest(response_source, expectation.resolved_source_hmac):
        raise DownloadBindingError("scoped download resolved to an unexpected source")
    if _bound_response_total(response, status) != expectation.content_length:
        raise DownloadBindingError("scoped download response size does not match approval")
    if expectation.last_modified is not None:
        response_last_modified = response.headers.get("Last-Modified", "")
        if (
            not isinstance(response_last_modified, str)
            or response_last_modified.strip() != expectation.last_modified
        ):
            raise DownloadBindingError("scoped download release metadata does not match approval")
    if status == 206:
        content_range = _content_range_parts(response)
        content_length = response.headers.get("Content-Length", "")
        if (
            content_range is None
            or requested_offset <= 0
            or not isinstance(content_length, str)
            or not content_length.isascii()
            or not content_length.isdecimal()
        ):
            raise DownloadBindingError("scoped download range does not match request")
        start, end, total = content_range
        if start != requested_offset or end != total - 1 or int(content_length) != end - start + 1:
            raise DownloadBindingError("scoped download range does not match request")


class _RetryableStatusError(Exception):
    """Internal: a response status code that should be retried (not surfaced)."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"retryable HTTP status {status_code}")
        self.status_code = status_code


class _SlowTransferError(Exception):
    """Internal: throughput fell below the floor — abort this attempt and retry.

    Retryable (a fresh connection often escapes a throttle), but deliberately
    *not* counted as forward progress by the retry budget even though the window
    appended bytes — otherwise a server that always throttles would reset the
    no-progress counter every window and the download would crawl indefinitely.
    """

    def __init__(self, rate_bps: float, floor_bps: float) -> None:
        super().__init__(f"throughput {rate_bps:,.0f} B/s below floor {floor_bps:,.0f} B/s")
        self.rate_bps = rate_bps
        self.floor_bps = floor_bps


# Exceptions that trigger a backoff-and-resume retry (flattened for ``except``).
_RETRY_TRIGGERS: tuple[type[BaseException], ...] = (
    *RETRYABLE_EXCEPTIONS,
    _RetryableStatusError,
    IncompleteDownloadError,
    _SlowTransferError,
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


def _content_range_parts(response: httpx.Response) -> tuple[int, int, int] | None:
    """Parse a satisfiable byte range as ``(start, end, total)``.

    This deliberately accepts only the canonical numeric form needed for an
    open-ended resume response.  Unsatisfied ranges (``bytes */N``), unknown
    totals, malformed bounds, and non-byte units return ``None``.
    """
    value = response.headers.get("Content-Range", "")
    if not isinstance(value, str):
        return None
    try:
        unit, remainder = value.strip().split(None, 1)
        bounds, total_text = remainder.split("/", 1)
        start_text, end_text = bounds.split("-", 1)
    except ValueError:
        return None
    numeric = (start_text, end_text, total_text)
    if unit.lower() != "bytes" or any(
        not part.isascii() or not part.isdecimal() for part in numeric
    ):
        return None
    start, end, total = (int(part) for part in numeric)
    if start < 0 or end < start or total <= end:
        return None
    return start, end, total


def _validator(response: httpx.Response) -> str | None:
    """Return a strong-ish ``If-Range`` validator (ETag preferred, else Last-Modified)."""
    return response.headers.get("ETag") or response.headers.get("Last-Modified")


def _safe_log_url(url: str) -> str:
    """Return a source URL safe for logs and user-facing download errors.

    **The path is untrusted here too.** Stripping userinfo, query and fragment
    while keeping ``parsed.path`` verbatim still leaks a path-based bearer or
    share token -- ``https://example.com/token/SECRET/file.tsv`` -- into the
    retry logs and into the reconstructed terminal error. This is the *generic*
    downloader, so unlike a module with a fixed set of canonical endpoints there
    is no URL here that can be assumed safe to echo: every caller supplies its
    own.

    The path is therefore reduced to a digest of the whole URL. Someone holding
    the URL can still confirm which one was used, and callers already log the
    context that identifies a transfer operationally -- database name and
    destination path -- so no diagnostic worth the leak is lost.
    """
    try:
        parsed = urlsplit(url)
        if not parsed.scheme or not parsed.hostname:
            return "<invalid download URL>"
        hostname = parsed.hostname
        host = f"[{hostname}]" if ":" in hostname else hostname
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return "<invalid download URL>"
    # No URL-reserved characters in the placeholder: this value is handed to
    # `httpx.Request` when a terminal error is rebuilt, and httpx would
    # percent-encode anything like angle brackets, so the string that reaches a
    # log would no longer equal the one produced here.
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return urlunsplit((parsed.scheme, f"{host}{port}", f"/redacted/{digest}", "", ""))


def _safe_error_text(exc: BaseException, *, raw_url: str, safe_url: str) -> str:
    """Remove the caller-supplied URL from a retry error before it is retained."""
    if isinstance(exc, RETRYABLE_EXCEPTIONS):
        # httpx transport messages can canonicalize or percent-encode a URL,
        # making substring replacement insufficient for signed credentials.
        return "transport failure"
    return str(exc).replace(raw_url, safe_url)


def _redacted_http_status_error(
    response: httpx.Response,
    *,
    safe_url: str,
) -> httpx.HTTPStatusError:
    """Rebuild a non-retryable HTTP error without retaining a signed request."""
    request = httpx.Request("GET", safe_url)
    safe_response = httpx.Response(
        response.status_code,
        request=request,
    )
    return httpx.HTTPStatusError(
        f"HTTP status {response.status_code} for {safe_url}",
        request=request,
        response=safe_response,
    )


def _validator_sidecar_path(tmp_path: Path) -> Path:
    """Path of the sidecar that persists a partial's ``If-Range`` validator."""
    return tmp_path.with_name(tmp_path.name + ".validator")


def read_validator_sidecar(tmp_path: Path) -> str | None:
    """Recover the persisted ``If-Range`` validator for a resumable partial.

    ``stream_download`` itself has no cross-run storage: a caller that wants a
    partial ``.tmp`` to survive a process restart (``resumable=True``) must also
    persist the validator the partial was associated with, so the next run can
    send ``If-Range`` and detect a rotated upstream instead of splicing new
    bytes onto a stale prefix. This reads that sidecar.

    Returns the stored ETag/Last-Modified only when **both** the sidecar and the
    ``tmp_path`` partial exist — a sidecar with no partial is meaningless (the
    next download starts at offset 0 and sends no ``If-Range``), so it is
    ignored rather than seeding a stale validator. Any read error degrades to
    ``None`` (a fresh, validator-less attempt), never a crash.
    """
    sidecar = _validator_sidecar_path(tmp_path)
    try:
        if not (tmp_path.exists() and sidecar.exists()):
            return None
        return sidecar.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def write_validator_sidecar(tmp_path: Path, validator: str) -> None:
    """Persist a partial's ``If-Range`` validator next to its ``.tmp``.

    Pass as ``on_validator`` to :func:`stream_download`: it fires the moment the
    validator is (re)captured, so a transfer that dies mid-stream still leaves
    the token on disk for the next process to validate its resume against.
    Persisting the validator must never break a live download, so any write
    error is logged and swallowed.
    """
    try:
        _validator_sidecar_path(tmp_path).write_text(validator, encoding="utf-8")
    except OSError as exc:
        logger.warning("validator_sidecar_write_failed", path=str(tmp_path), error=str(exc))


def clear_validator_sidecar(tmp_path: Path) -> None:
    """Remove a partial's validator sidecar (after the ``.tmp`` has been finalized)."""
    try:
        _validator_sidecar_path(tmp_path).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("validator_sidecar_clear_failed", path=str(tmp_path), error=str(exc))


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
    min_throughput_bps: float | None = DEFAULT_MIN_THROUGHPUT_BPS,
    stall_window: float = DEFAULT_STALL_WINDOW,
    extra_headers: Mapping[str, str] | None = None,
    resumable: bool = False,
    validator: str | None = None,
    on_validator: Callable[[str], None] | None = None,
    client_factory: Callable[[], httpx.Client] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
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
        timeout: Default for httpx's ``write`` / ``pool`` timeouts (seconds).
            NOT an overall transfer cap — httpx has no total timeout, so a slow
            transfer is bounded by ``read_timeout`` (a fully dead socket) and the
            ``min_throughput_bps`` watchdog (a throttle), not by this value.
        connect_timeout: Connect timeout (seconds).
        read_timeout: Per-read timeout (seconds) — bounds a *fully stalled*
            socket (no bytes at all for this long), not a slow trickle.
        chunk_size: Streaming chunk size (bytes).
        max_retries: Consecutive no-progress attempts tolerated before failing.
            A throttle abort counts as no-progress, so a persistent throttle
            fails after ~``max_retries`` windows instead of crawling.
        max_attempts: Absolute attempt ceiling regardless of progress.
        min_throughput_bps: Floor (bytes/sec) on sustained throughput over a
            ``stall_window``. Below it, the attempt is aborted and retried (a
            fresh connection often escapes a throttle). ``None`` disables the
            watchdog. Catches the throttled-but-alive case the read timeout
            can't (a trickle that keeps the socket technically alive).
        stall_window: Window (seconds) over which throughput is measured for the
            ``min_throughput_bps`` watchdog.
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
        monotonic: Injectable monotonic clock (tests drive it to simulate a
            throttle deterministically; defaults to :func:`time.monotonic`).

    Returns:
        :class:`DownloadOutcome` describing the completed transfer.

    Raises:
        DownloadError: On permanent failure (retry budget exhausted) or a
            non-retryable request failure. The partial tmp_path is removed
            before raising.
        httpx.HTTPStatusError: On a non-retryable HTTP status (e.g. 404), with
            a redacted request and response.
    """
    safe_url = _safe_log_url(url)
    strong_etag_scope = _STRONG_ETAG_DOWNLOAD_SCOPE.get()
    bound_expectation = (
        strong_etag_scope.expectation_for(url) if strong_etag_scope is not None else None
    )
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
    terminal_error: DownloadError | None = None

    def _cleanup_partial() -> None:
        if not resumable:
            # No cross-call resume: never leave a partial behind on failure.
            # Guard the cleanup so a unlink error (e.g. read-only dir) can't
            # mask the original download exception.
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError as cleanup_err:
                logger.warning(
                    "download_cleanup_failed", path=str(tmp_path), error=str(cleanup_err)
                )

    # Construct this only after leaving the ``except`` suite below. Raising a
    # replacement exception while a RequestError is active would retain the raw
    # request (and its signed query) as ``__context__``.
    terminal_request_error: DownloadError | None = None

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
            if bound_expectation is not None:
                if any(name.lower() == "if-match" for name in req_headers):
                    raise DownloadBindingError(
                        "scoped download cannot override the approved If-Match header"
                    )
                req_headers["If-Match"] = bound_expectation.etag
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
                    if bound_expectation is not None:
                        assert strong_etag_scope is not None
                        _verify_bound_response(
                            response,
                            bound_expectation,
                            offset,
                            strong_etag_scope.source_binding_key,
                        )
                        if status == 416:
                            # A pre-existing partial is not proof that this build
                            # consumed the approved response bytes.  Scoped builds
                            # require an actual, validator-bound 200/206 transfer.
                            raise DownloadBindingError(
                                "scoped download did not receive approved content"
                            )

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
                        raise _redacted_http_status_error(response, safe_url=safe_url)

                    # ── Stream the body ──
                    written = offset if mode == "ab" else 0
                    # Minimum-throughput watchdog state: bytes/time at the start of
                    # the current measurement window. ``read_timeout`` only catches
                    # a socket that goes fully silent; this catches one that keeps
                    # dribbling below the floor (the throttle that hangs forever).
                    watchdog_on = (
                        min_throughput_bps is not None
                        and min_throughput_bps > 0
                        and stall_window > 0
                    )
                    win_bytes = written
                    win_start = monotonic() if watchdog_on else 0.0
                    with open(tmp_path, mode) as f:
                        for chunk in response.iter_raw(chunk_size):
                            f.write(chunk)
                            written += len(chunk)
                            if progress_callback is not None:
                                progress_callback(written, expected_total)
                            if on_chunk is not None:
                                on_chunk(written)
                            if watchdog_on:
                                elapsed = monotonic() - win_start
                                if elapsed >= stall_window:
                                    rate = (written - win_bytes) / elapsed if elapsed > 0 else 0.0
                                    if rate < min_throughput_bps:
                                        raise _SlowTransferError(rate, min_throughput_bps)
                                    # Window met the floor — start a fresh window.
                                    win_bytes = written
                                    win_start = monotonic()

                # ── Stream ended cleanly — verify completeness ──
                final_size = tmp_path.stat().st_size if tmp_path.exists() else 0
                if expected_total is not None and final_size < expected_total:
                    raise IncompleteDownloadError(
                        f"received {final_size:,} of {expected_total:,} bytes from {safe_url}"
                    )
                if (
                    bound_expectation is not None
                    and final_size != bound_expectation.content_length
                ):
                    raise DownloadBindingError(
                        "scoped download payload size does not match approval"
                    )

                if strong_etag_scope is not None:
                    strong_etag_scope.mark_complete(url)

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
                #
                # A throttle abort is the exception: it DID append a (sub-floor)
                # window of bytes, but counting that as progress would let a
                # server that always throttles reset the budget every window and
                # crawl forever. Treat it as no-progress so a persistent throttle
                # exhausts ``max_retries`` and fails fast — while an *intermittent*
                # throttle is still rescued, because any later attempt that makes
                # genuine forward progress resets the counter.
                made_progress = new_offset > attempt_start_offset and not isinstance(
                    exc, _SlowTransferError
                )
                no_progress_failures = 0 if made_progress else no_progress_failures + 1
                resumed = True

                attempts_exhausted = attempt >= max_attempts
                budget_exhausted = no_progress_failures > max_retries
                if budget_exhausted or attempts_exhausted:
                    reason = "max_attempts" if attempts_exhausted else "max_retries"
                    terminal_error = DownloadError(
                        f"download failed after {attempt} attempt(s) "
                        f"({new_offset:,} bytes; {reason}): "
                        f"{type(exc).__name__}: "
                        f"{_safe_error_text(exc, raw_url=url, safe_url=safe_url)}"
                    )
                    break

                delay = compute_backoff(no_progress_failures or 1)
                logger.warning(
                    "download_retry",
                    url=safe_url,
                    attempt=attempt,
                    offset=new_offset,
                    made_progress=made_progress,
                    retry_in_s=round(delay, 2),
                    error=(
                        f"{type(exc).__name__}: "
                        f"{_safe_error_text(exc, raw_url=url, safe_url=safe_url)}"
                    ),
                )
                sleep(delay)
        assert terminal_error is not None
        raise terminal_error
    except httpx.RequestError:
        # RequestError subclasses retain the raw request object. Do not reuse
        # their message or type: either can carry caller-supplied signed URLs.
        # A fixed DownloadError is raised after the handler exits so it has no
        # raw exception in ``__cause__`` or ``__context__``.
        terminal_request_error = DownloadError(f"download request failed for {safe_url}")
    except BaseException:
        _cleanup_partial()
        raise

    assert terminal_request_error is not None
    _cleanup_partial()
    raise terminal_request_error
