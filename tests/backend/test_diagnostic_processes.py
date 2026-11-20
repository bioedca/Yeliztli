"""Exercise shipped service startup, helpers and the reload supervisor on disposable data."""

from __future__ import annotations

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest


def _environment(data: Path, **settings: str) -> dict[str, str]:
    return {
        **os.environ,
        "YELIZTLI_DATA_DIR": str(data),
        "YELIZTLI_HUEY_IMMEDIATE": "0",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        **settings,
    }


def _stop(proc):
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
    try:
        return proc.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate(timeout=5)
        raise


@pytest.mark.parametrize("module", ["backend.main", "backend.worker"])
def test_startup_failure_is_static_and_nonzero(tmp_path, module):
    result = subprocess.run(
        [sys.executable, "-m", module],
        cwd=tmp_path,
        env=_environment(tmp_path / "data", YELIZTLI_PORT="synthetic-invalid-configuration"),
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == "ERROR The service could not start or complete its work.\n"


@pytest.mark.parametrize("module", ["backend.main", "backend.worker"])
def test_existing_installation_stays_closed_before_cutover(tmp_path, module):
    data = tmp_path / "data"
    data.mkdir()
    queue = data / "huey.db"
    queue.write_bytes(b"fixture untouched")
    result = subprocess.run(
        [sys.executable, "-m", module],
        cwd=tmp_path,
        env=_environment(data),
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr == (
        "ERROR Diagnostic upgrade pending. Complete the operator cutover in the privacy guide.\n"
    )
    assert queue.read_bytes() == b"fixture untouched"
    assert list(data.iterdir()) == [queue]


def _status(proc, url):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        assert proc.poll() is None, "the service exited before becoming ready"
        try:
            with urlopen(url, timeout=2) as response:
                return response.headers, json.load(response)
        except (URLError, OSError, TimeoutError):
            time.sleep(0.15)
    pytest.fail("the disposable API did not become ready")


@pytest.mark.slow
def test_reload_keeps_console_safe_in_both_serving_processes(tmp_path):
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
    watched = tmp_path / "synthetic_watched_file.py"
    watched.write_text("# initial\n")
    data = tmp_path / "data"
    url = f"http://127.0.0.1:{port}/api/admin"
    proc = subprocess.Popen(
        [sys.executable, "-m", "backend.main"],
        cwd=tmp_path,
        env=_environment(data, YELIZTLI_PORT=str(port), YELIZTLI_DEBUG="true"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _status(proc, url + "/status")
        time.sleep(2.1)
        _, before = _status(proc, url + "/status")
        headers, result = _status(proc, url + "/logs?search=synthetic-console-query")
        assert headers["Cache-Control"] == "no-store" and result["total"] == 0
        watched.write_text("# reload\n")
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            _, after = _status(proc, url + "/status")
            if after["uptime_seconds"] < before["uptime_seconds"]:
                break
            time.sleep(0.2)
        else:
            pytest.fail("the watched server retained the original process")
        headers, result = _status(proc, url + "/logs?search=synthetic-console-query")
        assert headers["Cache-Control"] == "no-store" and result["total"] == 0
    finally:
        out, err = _stop(proc)
    assert out == ""
    assert err.count("INFO HTTP response 200") >= 4
    for marker in ("synthetic-console-query", watched.name, str(tmp_path)):
        assert marker not in err
    with sqlite3.connect(data / "reference.db") as db:
        rows = db.execute("SELECT message, event_data, logger FROM log_entries").fetchall()
    assert rows
    assert all("HTTP response 200" not in str(row) for row in rows)


@pytest.mark.slow
@pytest.mark.parametrize("capture_helper", [False, True])
def test_consumer_task_and_helper_use_only_the_reviewed_console(tmp_path, capture_helper):
    from backend.diagnostic_cutover import admit

    data = tmp_path / "data"
    admit(data)
    done = tmp_path / "task-executed"
    # The fixture registers a single disposable task. The shipped entrypoint
    # drives the real queue, worker and framework handlers.
    register = tmp_path / "fixture_consumer.py"
    register.write_text(
        """import os
import runpy
import subprocess
import sys
from pathlib import Path
from backend.operator_console import _isolate_output
_isolate_output()
from backend.tasks.huey_tasks import huey

@huey.task()
def example_task():
    print("fixture dependency output", flush=True)
    print("fixture dependency diagnostic", file=sys.stderr, flush=True)
    code = (
        'print("fixture helper output");'
        'import sys; print("fixture helper diagnostic", file=sys.stderr);'
        'from pathlib import Path; Path(sys.argv[1]).write_text("ran");'
        'sys.exit(1)'
    )
    result = subprocess.run([sys.executable, "-c", code, os.environ["FIXTURE_SIGNAL"]],
                            capture_output=os.environ["FIXTURE_CAPTURE"] == "1")
    if result.returncode:
        raise RuntimeError(result.stderr or "fixture task failure")

example_task()
sys.argv[1:] = ["-w", "1", "--no-periodic"]
runpy.run_module("backend.worker", run_name="__main__")
"""
    )
    proc = subprocess.Popen(
        [sys.executable, str(register)],
        cwd=tmp_path,
        env=_environment(data, FIXTURE_SIGNAL=str(done), FIXTURE_CAPTURE=str(int(capture_helper))),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 45
        while not done.exists() and time.monotonic() < deadline:
            assert proc.poll() is None
            time.sleep(0.15)
        assert done.read_text() == "ran"
        time.sleep(0.5)
    finally:
        out, err = _stop(proc)
    assert out == ""
    assert "Operation failed" in err
    assert "fixture" not in err
    with sqlite3.connect(data / "reference.db") as db:
        rows = db.execute("SELECT message, event_data, logger FROM log_entries").fetchall()
    assert all("fixture" not in str(row) for row in rows)


@pytest.mark.slow
def test_report_helper_and_application_failure_on_the_shipped_api(tmp_path):
    from urllib.error import HTTPError
    from urllib.request import Request

    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
    data = tmp_path / "data"
    done = tmp_path / "report-ran"
    launcher = tmp_path / "fixture_report.py"
    # Supply the report renderer a harmless helper and substitute only the
    # sample data boundary. The HTTP and service console routes are real.
    launcher.write_text(
        """import contextlib
import os
import subprocess
import sys
from backend.operator_console import _api, _run
def serve_fixture():
    from backend.main import app
    from backend.api.routes import reports
    from backend.reports import generator
    reports.require_fresh_sample = lambda *_: None
    reports.sample_export_guard = lambda *args, **kw: contextlib.nullcontext()
    generator.render_report_html = lambda *args, **kw: "fixture report"
    async def render(html):
        code = ('print("report helper stream");'
                'import sys; print("report helper diagnostic", file=sys.stderr);'
                'from pathlib import Path; Path(sys.argv[1]).write_text("executed")')
        subprocess.run([sys.executable, "-c", code, os.environ["FIXTURE_SIGNAL"]], check=True)
        return b"fixture PDF response"
    generator._html_to_pdf = render
    @app.get("/fixture-error")
    def fixture_error():
        raise RuntimeError("unreviewed fixture error")
    _api()

raise SystemExit(_run(serve_fixture))
"""
    )
    proc = subprocess.Popen(
        [sys.executable, str(launcher)],
        cwd=tmp_path,
        env=_environment(data, YELIZTLI_PORT=str(port), FIXTURE_SIGNAL=str(done)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _status(proc, base + "/api/health")
        req = Request(
            base + "/api/reports/generate",
            data=b'{"sample_id":1}',
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=10) as response:
            assert response.status == 200 and response.read() == b"fixture PDF response"
        assert done.read_text() == "executed"
        with pytest.raises(HTTPError) as failure:
            urlopen(base + "/fixture-error", timeout=10)
        assert failure.value.code == 500
        headers, log = _status(proc, base + "/api/admin/logs?search=report%20helper")
        assert headers["Cache-Control"] == "no-store" and log["total"] == 0
    finally:
        out, err = _stop(proc)
    assert out == ""
    assert "INFO HTTP response 200" in err and "Operation failed" in err
    for marker in ("report helper stream", "report helper diagnostic", "unreviewed fixture error"):
        assert marker not in err
    with sqlite3.connect(data / "reference.db") as db:
        rows = db.execute("SELECT message, event_data, logger FROM log_entries").fetchall()
    assert rows and all("helper" not in str(row) and "fixture" not in str(row) for row in rows)
