"""Focused regression tests for the FastAPI application lifespan."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

import backend.main as main


@pytest.fixture
def stub_lifespan_startup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    """Replace startup integrations so lifespan control flow is tested in isolation."""
    registry = SimpleNamespace(reference_engine=object())
    settings = SimpleNamespace(data_dir=tmp_path)

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "get_registry", lambda: registry)
    monkeypatch.setattr(main.reference_metadata, "create_all", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "ensure_reference_schema_current", lambda _engine: None)
    monkeypatch.setattr(main, "load_hla_proxy_data", lambda _engine: 0)
    monkeypatch.setattr(main, "check_genome_build_consistency", lambda _engine: {})
    monkeypatch.setattr(main, "configure_logging", lambda **_kwargs: None)
    monkeypatch.setattr(main, "warn_if_insecure_network_bind", lambda _settings: None)
    monkeypatch.setattr(main, "cleanup_interrupted_sessions", lambda _engine: None)
    monkeypatch.setattr(main, "recover_orphaned_jobs", lambda _engine: None)
    monkeypatch.setattr(main, "recover_orphaned_downloads", lambda _engine: None)
    return registry


async def test_lifespan_normal_exit_runs_teardown(
    monkeypatch: pytest.MonkeyPatch, stub_lifespan_startup: SimpleNamespace
) -> None:
    """A successful lifespan still shuts down the executor before the registry."""
    calls: list[str] = []
    monkeypatch.setattr(main, "shutdown_executor", lambda: calls.append("executor"))
    monkeypatch.setattr(main, "reset_registry", lambda: calls.append("registry"))

    async with main.lifespan(FastAPI()):
        pass

    assert calls == ["executor", "registry"]


async def test_lifespan_exception_preserves_trigger_when_cleanup_also_fails(
    monkeypatch: pytest.MonkeyPatch,
    stub_lifespan_startup: SimpleNamespace,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both cleanups run without masking an exception from the entered lifespan."""
    calls: list[str] = []
    triggering_error = RuntimeError("request handling failed")

    def fail_executor_cleanup() -> None:
        calls.append("executor")
        raise OSError("executor cleanup failed")

    def fail_registry_cleanup() -> None:
        calls.append("registry")
        raise ValueError("registry cleanup failed")

    monkeypatch.setattr(main, "shutdown_executor", fail_executor_cleanup)
    monkeypatch.setattr(main, "reset_registry", fail_registry_cleanup)

    with caplog.at_level(logging.ERROR, logger="backend.main"):
        with pytest.raises(RuntimeError, match="request handling failed") as exc_info:
            async with main.lifespan(FastAPI()):
                raise triggering_error

    assert exc_info.value is triggering_error
    assert calls == ["executor", "registry"]
    assert "download executor cleanup failed" in caplog.text
    assert "database registry cleanup failed" in caplog.text
    assert "preserving triggering RuntimeError" in caplog.text
    assert getattr(triggering_error, "__notes__", []) == [
        "download executor cleanup also failed: OSError: executor cleanup failed",
        "database registry cleanup also failed: ValueError: registry cleanup failed",
    ]


async def test_lifespan_cancellation_runs_teardown(
    monkeypatch: pytest.MonkeyPatch, stub_lifespan_startup: SimpleNamespace
) -> None:
    """Cancellation is propagated only after both teardown actions run."""
    calls: list[str] = []
    cancellation = asyncio.CancelledError("lifespan cancelled")
    monkeypatch.setattr(main, "shutdown_executor", lambda: calls.append("executor"))
    monkeypatch.setattr(main, "reset_registry", lambda: calls.append("registry"))

    with pytest.raises(asyncio.CancelledError, match="lifespan cancelled") as exc_info:
        async with main.lifespan(FastAPI()):
            raise cancellation

    assert exc_info.value is cancellation
    assert calls == ["executor", "registry"]


async def test_lifespan_clean_exit_propagates_cleanup_failure_after_both_attempts(
    monkeypatch: pytest.MonkeyPatch, stub_lifespan_startup: SimpleNamespace
) -> None:
    """A teardown error remains visible when there is no earlier exception."""
    calls: list[str] = []
    cleanup_error = OSError("executor cleanup failed")

    def fail_executor_cleanup() -> None:
        calls.append("executor")
        raise cleanup_error

    monkeypatch.setattr(main, "shutdown_executor", fail_executor_cleanup)
    monkeypatch.setattr(main, "reset_registry", lambda: calls.append("registry"))

    with pytest.raises(OSError, match="executor cleanup failed") as exc_info:
        async with main.lifespan(FastAPI()):
            pass

    assert exc_info.value is cleanup_error
    assert calls == ["executor", "registry"]


async def test_lifespan_partial_startup_failure_runs_teardown_after_registry_acquisition(
    monkeypatch: pytest.MonkeyPatch, stub_lifespan_startup: SimpleNamespace
) -> None:
    """Startup failures are cleaned up once the registry has been acquired."""
    calls: list[str] = []
    triggering_error = RuntimeError("schema migration failed")

    def fail_schema_check(_engine: object) -> None:
        raise triggering_error

    monkeypatch.setattr(main, "ensure_reference_schema_current", fail_schema_check)
    monkeypatch.setattr(main, "shutdown_executor", lambda: calls.append("executor"))
    monkeypatch.setattr(main, "reset_registry", lambda: calls.append("registry"))

    with pytest.raises(RuntimeError, match="schema migration failed") as exc_info:
        async with main.lifespan(FastAPI()):
            pytest.fail("lifespan body must not be entered after startup failure")

    assert exc_info.value is triggering_error
    assert calls == ["executor", "registry"]
