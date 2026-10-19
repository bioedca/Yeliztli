"""The backend suite stays off the real network (#2538).

``tests/backend/conftest.py`` pins the bundle manifest to the committed file and
fails any test that reaches a non-loopback host. These pin both halves. The
guard tests drive :mod:`_no_network` directly, so a refused lookup made on
purpose here does not fail this test's own autouse check.
"""

from __future__ import annotations

import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from _isolated_data_home import REPO_MANIFEST
from _no_network import begin, end

from backend.db import manifest


def test_the_session_pins_the_committed_manifest() -> None:
    assert os.environ[manifest.MANIFEST_PATH_ENV] == str(REPO_MANIFEST)
    committed = json.loads(REPO_MANIFEST.read_text(encoding="utf-8"))["bundles"]
    name, entry = next(iter(committed.items()))
    resolved = manifest.get_bundle_info(name)
    assert resolved is not None
    assert resolved.version == entry["version"]


def test_a_lookup_of_a_real_host_is_refused_and_failed() -> None:
    begin(enforce=True)
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("raw.githubusercontent.com", 443)
    reached = end()
    begin(enforce=True)  # hand this test's autouse check a clean record
    assert len(reached) == 1
    assert reached[0].startswith("DNS lookup of raw.githubusercontent.com")


def test_a_connect_to_a_real_address_is_refused_and_failed() -> None:
    begin(enforce=True)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            sock.connect(("185.199.108.133", 443))
        assert sock.connect_ex(("185.199.108.133", 443)) != 0
    finally:
        sock.close()
    reached = end()
    begin(enforce=True)
    assert [line.split(" (thread")[0] for line in reached] == [
        "connect to 185.199.108.133",
        "connect to 185.199.108.133",
    ]


class _Ok(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


def test_loopback_traffic_is_allowed() -> None:
    import httpx

    server = HTTPServer(("127.0.0.1", 0), _Ok)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        host, port = server.server_address
        begin(enforce=True)
        assert httpx.get(f"http://{host}:{port}/", timeout=5).status_code == 204
        assert socket.getaddrinfo("localhost", port)
        assert end() == []
    finally:
        begin(enforce=True)
        server.shutdown()
