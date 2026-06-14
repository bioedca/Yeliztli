"""Tests for the resilient HTTP download helper (backend.annotation.http_download).

Exercised against a real local HTTP server (so Range / resume / drop behaviour
is genuine, not mocked) plus a few fake-client unit tests for branches that are
hard to provoke with real httpx (clean short body, header construction).
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from backend.annotation.http_download import (
    DownloadError,
    DownloadOutcome,
    _content_range_total,
    compute_backoff,
    stream_download,
)

# 256 KiB of structured data so byte offsets are meaningful and resumes
# reassemble to exactly the original.
TEST_DATA = bytes((i % 256) for i in range(256 * 1024))

NOOP_SLEEP = lambda _delay: None  # noqa: E731 (terse no-op for injected backoff)


# ═══════════════════════════════════════════════════════════════════════
# Local HTTP server scaffolding
# ═══════════════════════════════════════════════════════════════════════


def _serve(handler_cls: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}/file.bin"


def _parse_range_start(range_header: str) -> int:
    # "bytes=START-" / "bytes=START-END"
    return int(range_header.split("=", 1)[1].split("-", 1)[0])


# ═══════════════════════════════════════════════════════════════════════
# Pure-function unit tests
# ═══════════════════════════════════════════════════════════════════════


def test_compute_backoff_within_bounds() -> None:
    for attempt in range(1, 10):
        delay = compute_backoff(attempt, base=1.0, maximum=30.0)
        assert 0.0 <= delay <= 30.0


def test_content_range_total_parses_total() -> None:
    resp = httpx.Response(206, headers={"Content-Range": "bytes 100-199/5000"})
    assert _content_range_total(resp) == 5000


def test_content_range_total_does_not_fall_back_to_content_length() -> None:
    # On a 206, Content-Length is the *range* length, not the whole file — the
    # parser must return None rather than mistake it for the total.
    resp = httpx.Response(206, headers={"Content-Length": "1234"})
    assert _content_range_total(resp) is None


def test_content_range_total_unknown_is_none() -> None:
    resp = httpx.Response(206, headers={"Content-Range": "bytes 0-9/*"})
    assert _content_range_total(resp) is None


# ═══════════════════════════════════════════════════════════════════════
# Happy path
# ═══════════════════════════════════════════════════════════════════════


def test_full_download_happy_path(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(TEST_DATA)))
            self.end_headers()
            self.wfile.write(TEST_DATA)

        def log_message(self, *a: object) -> None:  # noqa: A002
            pass

    server, url = _serve(Handler)
    try:
        tmp = tmp_path / "out.bin.tmp"
        outcome = stream_download(url, tmp, sleep=NOOP_SLEEP)
        assert isinstance(outcome, DownloadOutcome)
        assert outcome.total_bytes == len(TEST_DATA)
        assert outcome.expected_total == len(TEST_DATA)
        assert outcome.attempts == 1
        assert outcome.resumed is False
        assert tmp.read_bytes() == TEST_DATA
    finally:
        server.shutdown()


def test_progress_callback_monotonic_and_complete(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(TEST_DATA)))
            self.end_headers()
            self.wfile.write(TEST_DATA)

        def log_message(self, *a: object) -> None:  # noqa: A002
            pass

    server, url = _serve(Handler)
    seen: list[int] = []
    try:
        tmp = tmp_path / "out.bin.tmp"
        stream_download(
            url,
            tmp,
            progress_callback=lambda written, total: seen.append(written),
            chunk_size=8192,
            sleep=NOOP_SLEEP,
        )
        assert seen == sorted(seen)  # monotonic
        assert seen[-1] == len(TEST_DATA)
    finally:
        server.shutdown()


def test_identity_encoding_header_sent(tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            captured["Accept-Encoding"] = self.headers.get("Accept-Encoding", "")
            self.send_response(200)
            self.send_header("Content-Length", str(len(TEST_DATA)))
            self.end_headers()
            self.wfile.write(TEST_DATA)

        def log_message(self, *a: object) -> None:  # noqa: A002
            pass

    server, url = _serve(Handler)
    try:
        stream_download(url, tmp_path / "out.bin.tmp", sleep=NOOP_SLEEP)
        assert captured["Accept-Encoding"] == "identity"
    finally:
        server.shutdown()


# ═══════════════════════════════════════════════════════════════════════
# Resume after a mid-stream connection drop (the reported failure mode)
# ═══════════════════════════════════════════════════════════════════════


def test_resume_after_midstream_drop(tmp_path: Path) -> None:
    drop_after = 100_000

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        dropped = False

        def do_GET(self) -> None:
            range_header = self.headers.get("Range")
            if range_header:
                start = _parse_range_start(range_header)
                self.send_response(206)
                self.send_header(
                    "Content-Range", f"bytes {start}-{len(TEST_DATA) - 1}/{len(TEST_DATA)}"
                )
                self.send_header("Content-Length", str(len(TEST_DATA) - start))
                self.end_headers()
                self.wfile.write(TEST_DATA[start:])
                return
            if not type(self).dropped:
                # First request: advertise full length but hang up early to
                # mimic "peer closed connection without sending complete body".
                type(self).dropped = True
                self.send_response(200)
                self.send_header("Content-Length", str(len(TEST_DATA)))
                self.end_headers()
                self.wfile.write(TEST_DATA[:drop_after])
                self.close_connection = True
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(TEST_DATA)))
            self.end_headers()
            self.wfile.write(TEST_DATA)

        def log_message(self, *a: object) -> None:  # noqa: A002
            pass

    server, url = _serve(Handler)
    try:
        tmp = tmp_path / "out.bin.tmp"
        outcome = stream_download(url, tmp, chunk_size=8192, sleep=NOOP_SLEEP)
        assert tmp.read_bytes() == TEST_DATA
        assert outcome.total_bytes == len(TEST_DATA)
        assert outcome.attempts >= 2
        assert outcome.resumed is True
    finally:
        server.shutdown()


def test_server_ignoring_range_restarts_cleanly(tmp_path: Path) -> None:
    """A server that drops once then ignores Range still completes via restart."""
    drop_after = 50_000

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        dropped = False

        def do_GET(self) -> None:
            # Always 200 full body, ignoring any Range header.
            if not type(self).dropped:
                type(self).dropped = True
                self.send_response(200)
                self.send_header("Content-Length", str(len(TEST_DATA)))
                self.end_headers()
                self.wfile.write(TEST_DATA[:drop_after])
                self.close_connection = True
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(TEST_DATA)))
            self.end_headers()
            self.wfile.write(TEST_DATA)

        def log_message(self, *a: object) -> None:  # noqa: A002
            pass

    server, url = _serve(Handler)
    try:
        tmp = tmp_path / "out.bin.tmp"
        outcome = stream_download(url, tmp, chunk_size=8192, sleep=NOOP_SLEEP)
        assert tmp.read_bytes() == TEST_DATA
        assert outcome.attempts >= 2
    finally:
        server.shutdown()


# ═══════════════════════════════════════════════════════════════════════
# Retryable status codes
# ═══════════════════════════════════════════════════════════════════════


def test_retryable_503_then_success(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        served_error = False

        def do_GET(self) -> None:
            if not type(self).served_error:
                type(self).served_error = True
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(TEST_DATA)))
            self.end_headers()
            self.wfile.write(TEST_DATA)

        def log_message(self, *a: object) -> None:  # noqa: A002
            pass

    server, url = _serve(Handler)
    try:
        tmp = tmp_path / "out.bin.tmp"
        outcome = stream_download(url, tmp, sleep=NOOP_SLEEP)
        assert tmp.read_bytes() == TEST_DATA
        assert outcome.attempts >= 2
    finally:
        server.shutdown()


def test_persistent_5xx_exhausts_and_cleans_up(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a: object) -> None:  # noqa: A002
            pass

    server, url = _serve(Handler)
    try:
        tmp = tmp_path / "out.bin.tmp"
        with pytest.raises(DownloadError) as exc_info:
            stream_download(url, tmp, max_retries=2, sleep=NOOP_SLEEP)
        assert "500" in str(exc_info.value)
        assert not tmp.exists()  # cleaned up (resumable=False default)
    finally:
        server.shutdown()


def test_non_retryable_404_raises_and_cleans_up(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a: object) -> None:  # noqa: A002
            pass

    server, url = _serve(Handler)
    try:
        tmp = tmp_path / "out.bin.tmp"
        with pytest.raises(httpx.HTTPStatusError):
            stream_download(url, tmp, max_retries=2, sleep=NOOP_SLEEP)
        assert not tmp.exists()
    finally:
        server.shutdown()


# ═══════════════════════════════════════════════════════════════════════
# resumable=True keeps the partial across calls
# ═══════════════════════════════════════════════════════════════════════


def test_resumable_keeps_partial_on_failure(tmp_path: Path) -> None:
    """With resumable=True, a partial download survives a permanent failure."""
    drop_after = 40_000

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            # Serve a chunk then always drop — never completes.
            self.send_response(200)
            self.send_header("Content-Length", str(len(TEST_DATA)))
            self.end_headers()
            self.wfile.write(TEST_DATA[:drop_after])
            self.close_connection = True

        def log_message(self, *a: object) -> None:  # noqa: A002
            pass

    server, url = _serve(Handler)
    try:
        tmp = tmp_path / "out.bin.tmp"
        with pytest.raises(DownloadError):
            stream_download(
                url, tmp, chunk_size=8192, max_retries=2, resumable=True, sleep=NOOP_SLEEP
            )
        # Partial preserved for a later resume (resume granularity is one chunk,
        # so the persisted size is a multiple of chunk_size up to drop_after).
        assert tmp.exists()
        assert 0 < tmp.stat().st_size <= drop_after
    finally:
        server.shutdown()


# ═══════════════════════════════════════════════════════════════════════
# Fake-client unit tests for hard-to-provoke branches
# ═══════════════════════════════════════════════════════════════════════


class _FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str], chunks: Iterable[bytes]) -> None:
        self.status_code = status_code
        self.headers = httpx.Headers(headers)
        self._chunks = list(chunks)

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def iter_raw(self, chunk_size: int = 65536) -> Iterator[bytes]:
        yield from self._chunks

    def raise_for_status(self) -> None:
        request = httpx.Request("GET", "http://fake/")
        response = httpx.Response(self.status_code, request=request)
        raise httpx.HTTPStatusError("error", request=request, response=response)


class _FakeClient:
    def __init__(self, response: _FakeResponse, sink: list[dict[str, str]]) -> None:
        self._response = response
        self._sink = sink

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def stream(
        self, method: str, url: str, headers: dict[str, str] | None = None
    ) -> _FakeResponse:
        self._sink.append(dict(headers or {}))
        return self._response


def _client_factory(responses: list[_FakeResponse], sink: list[dict[str, str]]):
    it = iter(responses)

    def factory() -> _FakeClient:
        return _FakeClient(next(it), sink)

    return factory


def test_clean_short_body_retries_then_resumes(tmp_path: Path) -> None:
    """A body that ends cleanly but short of Content-Length is retried + resumed."""
    sink: list[dict[str, str]] = []
    total = 100
    responses = [
        # Attempt 1: advertise 100, deliver only 60, end cleanly -> Incomplete.
        _FakeResponse(200, {"Content-Length": str(total)}, [bytes(60)]),
        # Attempt 2: Range 60-, deliver remaining 40 -> complete.
        _FakeResponse(
            206,
            {"Content-Range": f"bytes 60-{total - 1}/{total}", "Content-Length": "40"},
            [bytes(40)],
        ),
    ]
    tmp = tmp_path / "short.bin.tmp"
    outcome = stream_download(
        "http://fake/file.bin",
        tmp,
        client_factory=_client_factory(responses, sink),
        sleep=NOOP_SLEEP,
    )
    assert outcome.total_bytes == total
    assert tmp.stat().st_size == total
    # Second request carried a Range header from the partial.
    assert sink[1].get("Range") == "bytes=60-"
    assert sink[0].get("Accept-Encoding") == "identity"


def test_incomplete_raised_when_retries_exhausted(tmp_path: Path) -> None:
    sink: list[dict[str, str]] = []
    responses = [
        _FakeResponse(200, {"Content-Length": "100"}, [bytes(10)]),
        # Resume keeps returning short bodies with no forward progress beyond 10.
        _FakeResponse(206, {"Content-Range": "bytes 10-99/100"}, []),
        _FakeResponse(206, {"Content-Range": "bytes 10-99/100"}, []),
        _FakeResponse(206, {"Content-Range": "bytes 10-99/100"}, []),
    ]
    tmp = tmp_path / "stuck.bin.tmp"
    with pytest.raises(DownloadError):
        stream_download(
            "http://fake/file.bin",
            tmp,
            client_factory=_client_factory(responses, sink),
            max_retries=2,
            sleep=NOOP_SLEEP,
        )
    assert not tmp.exists()


def test_if_range_validator_sent_on_resume(tmp_path: Path) -> None:
    sink: list[dict[str, str]] = []
    total = 100
    responses = [
        _FakeResponse(
            200,
            {"Content-Length": str(total), "ETag": '"v1"'},
            [bytes(40)],  # short -> triggers a resume
        ),
        _FakeResponse(
            206,
            {"Content-Range": f"bytes 40-{total - 1}/{total}"},
            [bytes(60)],
        ),
    ]
    tmp = tmp_path / "etag.bin.tmp"
    stream_download(
        "http://fake/file.bin",
        tmp,
        client_factory=_client_factory(responses, sink),
        sleep=NOOP_SLEEP,
    )
    # The resume request must carry both Range and If-Range(validator).
    assert sink[1].get("Range") == "bytes=40-"
    assert sink[1].get("If-Range") == '"v1"'


def test_416_already_complete_returns_success(tmp_path: Path) -> None:
    """A 416 whose Content-Range total equals what we hold means we're done."""
    sink: list[dict[str, str]] = []
    tmp = tmp_path / "done.bin.tmp"
    tmp.write_bytes(bytes(100))  # pre-existing complete partial
    responses = [_FakeResponse(416, {"Content-Range": "bytes */100"}, [])]
    outcome = stream_download(
        "http://fake/file.bin",
        tmp,
        client_factory=_client_factory(responses, sink),
        resumable=True,
        sleep=NOOP_SLEEP,
    )
    assert outcome.total_bytes == 100
    assert outcome.expected_total == 100
    assert sink[0].get("Range") == "bytes=100-"


def test_if_range_revalidates_after_upstream_rotation(tmp_path: Path) -> None:
    """When the upstream rotates (200 restart), the validator is re-captured.

    Otherwise every subsequent resume would send the stale validator, the server
    would keep returning 200, and the transfer could never resume to completion.
    """
    sink: list[dict[str, str]] = []
    total = 100
    responses = [
        # v1: short body -> resume.
        _FakeResponse(200, {"Content-Length": str(total), "ETag": '"v1"'}, [bytes(40)]),
        # Resume with If-Range:v1; server rotated -> 200 (ignores Range), v2, short.
        _FakeResponse(200, {"Content-Length": str(total), "ETag": '"v2"'}, [bytes(50)]),
        # Resume must now carry If-Range:v2 -> 206 completes.
        _FakeResponse(206, {"Content-Range": f"bytes 50-{total - 1}/{total}"}, [bytes(50)]),
    ]
    tmp = tmp_path / "rotate.bin.tmp"
    outcome = stream_download(
        "http://fake/file.bin",
        tmp,
        client_factory=_client_factory(responses, sink),
        sleep=NOOP_SLEEP,
    )
    assert outcome.total_bytes == total
    assert sink[1].get("If-Range") == '"v1"'  # first resume used the original
    assert sink[2].get("If-Range") == '"v2"'  # after rotation, re-captured


def test_max_attempts_ceiling_enforced(tmp_path: Path) -> None:
    """Forward progress every attempt still terminates at the max_attempts ceiling."""
    sink: list[dict[str, str]] = []
    total = 100
    # Each attempt appends exactly one byte then ends short -> progress every time,
    # so the no-progress budget never trips; only max_attempts can stop it.
    responses = [_FakeResponse(200, {"Content-Length": str(total)}, [bytes(1)])]
    for start in range(1, 5):
        responses.append(
            _FakeResponse(206, {"Content-Range": f"bytes {start}-{total - 1}/{total}"}, [bytes(1)])
        )
    tmp = tmp_path / "dribble.bin.tmp"
    with pytest.raises(DownloadError) as exc_info:
        stream_download(
            "http://fake/file.bin",
            tmp,
            client_factory=_client_factory(responses, sink),
            max_retries=10,
            max_attempts=5,
            sleep=NOOP_SLEEP,
        )
    assert "max_attempts" in str(exc_info.value)


def test_206_unknown_total_completes_without_false_incomplete(tmp_path: Path) -> None:
    """A 206 with an unknown total (bytes X-Y/*) completes without a bogus check."""
    sink: list[dict[str, str]] = []
    tmp = tmp_path / "unknown.bin.tmp"
    tmp.write_bytes(bytes(50))
    responses = [_FakeResponse(206, {"Content-Range": "bytes 50-99/*"}, [bytes(50)])]
    outcome = stream_download(
        "http://fake/file.bin",
        tmp,
        client_factory=_client_factory(responses, sink),
        resumable=True,
        sleep=NOOP_SLEEP,
    )
    assert outcome.expected_total is None
    assert outcome.total_bytes == 100


# ── Durable If-Range validator (PR-15) ───────────────────────────────


def test_on_validator_fires_on_fresh_download(tmp_path: Path) -> None:
    """A fresh download captures the server's ETag and reports it both via the
    on_validator callback (for mid-transfer persistence) and on the outcome."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("ETag", '"v2"')
            self.send_header("Content-Length", str(len(TEST_DATA)))
            self.end_headers()
            self.wfile.write(TEST_DATA)

        def log_message(self, *a: object) -> None:  # noqa: A002
            pass

    server, url = _serve(Handler)
    try:
        captured: list[str] = []
        outcome = stream_download(
            url, tmp_path / "out.tmp", on_validator=captured.append, sleep=NOOP_SLEEP
        )
        assert outcome.validator == '"v2"'
        assert captured == ['"v2"']
    finally:
        server.shutdown()


def test_seeded_validator_resumes_via_range_when_unchanged(tmp_path: Path) -> None:
    """A seeded validator matching the live resource lets the first Range request
    resume the existing partial (206) instead of restarting."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            range_header = self.headers.get("Range")
            if_range = self.headers.get("If-Range")
            # Unchanged resource: honor the conditional Range.
            if range_header and if_range == '"v1"':
                start = _parse_range_start(range_header)
                self.send_response(206)
                self.send_header("ETag", '"v1"')
                self.send_header(
                    "Content-Range", f"bytes {start}-{len(TEST_DATA) - 1}/{len(TEST_DATA)}"
                )
                self.send_header("Content-Length", str(len(TEST_DATA) - start))
                self.end_headers()
                self.wfile.write(TEST_DATA[start:])
                return
            self.send_response(200)
            self.send_header("ETag", '"v1"')
            self.send_header("Content-Length", str(len(TEST_DATA)))
            self.end_headers()
            self.wfile.write(TEST_DATA)

        def log_message(self, *a: object) -> None:  # noqa: A002
            pass

    server, url = _serve(Handler)
    try:
        tmp = tmp_path / "out.tmp"
        tmp.write_bytes(TEST_DATA[:50_000])  # a genuine prefix of the live body
        outcome = stream_download(url, tmp, resumable=True, validator='"v1"', sleep=NOOP_SLEEP)
        assert tmp.read_bytes() == TEST_DATA
        assert outcome.resumed is True
    finally:
        server.shutdown()


def test_seeded_validator_forces_clean_restart_on_rotation(tmp_path: Path) -> None:
    """The core guard: a STALE seeded validator makes the server reject the
    conditional Range (If-Range mismatch → full 200), so the rotated body fully
    replaces the stale partial instead of being spliced onto it."""

    new_body = bytes(((i + 7) % 256) for i in range(120_000))

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            range_header = self.headers.get("Range")
            if_range = self.headers.get("If-Range")
            # Resource has rotated to ETag "v2"; a stale If-Range ("v1") must NOT
            # be honored — return the full current body.
            if range_header and if_range == '"v2"':
                start = _parse_range_start(range_header)
                self.send_response(206)
                self.send_header("ETag", '"v2"')
                self.send_header(
                    "Content-Range", f"bytes {start}-{len(new_body) - 1}/{len(new_body)}"
                )
                self.send_header("Content-Length", str(len(new_body) - start))
                self.end_headers()
                self.wfile.write(new_body[start:])
                return
            self.send_response(200)
            self.send_header("ETag", '"v2"')
            self.send_header("Content-Length", str(len(new_body)))
            self.end_headers()
            self.wfile.write(new_body)

        def log_message(self, *a: object) -> None:  # noqa: A002
            pass

    server, url = _serve(Handler)
    try:
        tmp = tmp_path / "out.tmp"
        tmp.write_bytes(b"STALE-PARTIAL-FROM-OLD-RESOURCE" * 100)  # not a prefix of new_body
        captured: list[str] = []
        outcome = stream_download(
            url,
            tmp,
            resumable=True,
            validator='"v1"',  # stale — server will reject the conditional Range
            on_validator=captured.append,
            sleep=NOOP_SLEEP,
        )
        # Clean replacement, byte-for-byte — no splice corruption.
        assert tmp.read_bytes() == new_body
        # The new resource's validator is re-captured + surfaced for persistence.
        assert outcome.validator == '"v2"'
        assert captured == ['"v2"']
    finally:
        server.shutdown()
