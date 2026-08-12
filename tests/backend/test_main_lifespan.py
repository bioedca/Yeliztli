import asyncio
import logging
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI

from backend.config import Settings
from backend.main import lifespan


class LifespanBodyError(RuntimeError):
    """Sentinel raised from inside an entered application lifespan."""


@pytest.fixture
def lifespan_dependencies(tmp_path: Path) -> Iterator[SimpleNamespace]:
    """Replace startup integrations while preserving lifespan control flow."""
    settings = Settings(data_dir=tmp_path, wal_mode=False)
    registry = SimpleNamespace(reference_engine=object())

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.main.get_registry", return_value=registry) as get_registry,
        patch("backend.main.bootstrap_reference_schema_tables") as bootstrap_schema,
        patch("backend.main.ensure_reference_schema_current") as ensure_schema,
        patch("backend.main.load_hla_proxy_data", return_value=0),
        patch("backend.main.check_genome_build_consistency", return_value={}),
        patch("backend.main.configure_logging"),
        patch("backend.main.warn_if_insecure_network_bind"),
        patch("backend.main.cleanup_interrupted_sessions"),
        patch("backend.main.recover_orphaned_jobs"),
        patch("backend.main.recover_orphaned_downloads"),
        patch("backend.main.shutdown_executor") as shutdown_executor,
        patch("backend.main.reset_registry") as reset_registry,
    ):
        yield SimpleNamespace(
            bootstrap_schema=bootstrap_schema,
            ensure_schema=ensure_schema,
            get_registry=get_registry,
            reset_registry=reset_registry,
            shutdown_executor=shutdown_executor,
        )


@pytest.mark.asyncio
async def test_lifespan_exception_runs_teardown_and_preserves_original(
    lifespan_dependencies: SimpleNamespace,
) -> None:
    original = LifespanBodyError("request loop failed")

    with pytest.raises(LifespanBodyError) as caught:
        async with lifespan(FastAPI()):
            raise original

    assert caught.value is original
    lifespan_dependencies.shutdown_executor.assert_called_once_with()
    lifespan_dependencies.reset_registry.assert_called_once_with()


@pytest.mark.asyncio
async def test_lifespan_cancellation_runs_teardown_and_preserves_original(
    lifespan_dependencies: SimpleNamespace,
) -> None:
    original = asyncio.CancelledError("server cancelled")
    lifespan_dependencies.shutdown_executor.side_effect = RuntimeError("executor stuck")

    with pytest.raises(asyncio.CancelledError) as caught:
        async with lifespan(FastAPI()):
            raise original

    assert caught.value is original
    lifespan_dependencies.shutdown_executor.assert_called_once_with()
    lifespan_dependencies.reset_registry.assert_called_once_with()


@pytest.mark.asyncio
async def test_lifespan_normal_exit_runs_teardown(
    lifespan_dependencies: SimpleNamespace,
) -> None:
    teardown_order: list[str] = []
    lifespan_dependencies.shutdown_executor.side_effect = lambda: teardown_order.append(
        "shutdown_executor"
    )
    lifespan_dependencies.reset_registry.side_effect = lambda: teardown_order.append(
        "reset_registry"
    )

    async with lifespan(FastAPI()):
        lifespan_dependencies.shutdown_executor.assert_not_called()
        lifespan_dependencies.reset_registry.assert_not_called()

    lifespan_dependencies.bootstrap_schema.assert_called_once_with(
        lifespan_dependencies.get_registry.return_value.reference_engine
    )
    lifespan_dependencies.shutdown_executor.assert_called_once_with()
    lifespan_dependencies.reset_registry.assert_called_once_with()
    assert teardown_order == ["shutdown_executor", "reset_registry"]


@pytest.mark.asyncio
async def test_lifespan_registry_startup_failure_runs_safe_teardown(
    lifespan_dependencies: SimpleNamespace,
) -> None:
    original = RuntimeError("registry startup failed")
    lifespan_dependencies.get_registry.side_effect = original

    with pytest.raises(RuntimeError) as caught:
        async with lifespan(FastAPI()):
            pytest.fail("lifespan body must not run after startup failure")

    assert caught.value is original
    lifespan_dependencies.shutdown_executor.assert_called_once_with()
    lifespan_dependencies.reset_registry.assert_called_once_with()


@pytest.mark.asyncio
async def test_lifespan_partial_startup_failure_runs_teardown(
    lifespan_dependencies: SimpleNamespace,
) -> None:
    original = RuntimeError("schema migration failed")
    lifespan_dependencies.ensure_schema.side_effect = original

    with pytest.raises(RuntimeError) as caught:
        async with lifespan(FastAPI()):
            pytest.fail("lifespan body must not run after startup failure")

    assert caught.value is original
    lifespan_dependencies.shutdown_executor.assert_called_once_with()
    lifespan_dependencies.reset_registry.assert_called_once_with()


@pytest.mark.asyncio
async def test_cleanup_failures_do_not_mask_original_exception(
    lifespan_dependencies: SimpleNamespace,
    caplog: pytest.LogCaptureFixture,
) -> None:
    original = LifespanBodyError("request loop failed")
    lifespan_dependencies.shutdown_executor.side_effect = RuntimeError("executor stuck")
    lifespan_dependencies.reset_registry.side_effect = RuntimeError("registry stuck")

    with (
        caplog.at_level(logging.ERROR, logger="backend.main"),
        pytest.raises(LifespanBodyError) as caught,
    ):
        async with lifespan(FastAPI()):
            raise original

    assert caught.value is original
    lifespan_dependencies.shutdown_executor.assert_called_once_with()
    lifespan_dependencies.reset_registry.assert_called_once_with()
    assert "FastAPI lifespan cleanup failed for download executor" in caplog.text
    assert "FastAPI lifespan cleanup failed for database registry" in caplog.text
    assert "executor stuck" in caplog.text
    assert "registry stuck" in caplog.text


@pytest.mark.asyncio
async def test_normal_cleanup_failure_remains_visible(
    lifespan_dependencies: SimpleNamespace,
) -> None:
    cleanup_error = RuntimeError("executor stuck")
    lifespan_dependencies.shutdown_executor.side_effect = cleanup_error

    with pytest.raises(RuntimeError) as caught:
        async with lifespan(FastAPI()):
            pass

    assert caught.value is cleanup_error
    lifespan_dependencies.shutdown_executor.assert_called_once_with()
    lifespan_dependencies.reset_registry.assert_called_once_with()


@pytest.mark.asyncio
async def test_normal_dual_cleanup_failures_are_grouped(
    lifespan_dependencies: SimpleNamespace,
) -> None:
    executor_error = RuntimeError("executor stuck")
    registry_error = ValueError("registry stuck")
    lifespan_dependencies.shutdown_executor.side_effect = executor_error
    lifespan_dependencies.reset_registry.side_effect = registry_error

    with pytest.raises(ExceptionGroup) as caught:
        async with lifespan(FastAPI()):
            pass

    assert caught.value.exceptions == (executor_error, registry_error)
    lifespan_dependencies.shutdown_executor.assert_called_once_with()
    lifespan_dependencies.reset_registry.assert_called_once_with()


@pytest.mark.asyncio
async def test_startup_survives_a_directory_it_cannot_make_private(
    lifespan_dependencies: SimpleNamespace,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A data directory that cannot be made private is reported, never fatal.

    The warning exists precisely so the setup wizard can offer another path,
    which it can only do if the application finishes starting. Handing the
    detail to a stdlib logger as an arbitrary keyword raises ``TypeError`` out
    of ``Logger._log`` and aborts startup at exactly the point this branch is
    meant to keep survivable (#2163).
    """
    problem = "Directory at /srv/data is group- or world-writable"
    with (
        patch(
            "backend.main.ensure_private_directory",
            return_value=problem,
        ) as ensure_private,
        caplog.at_level(logging.WARNING, logger="backend.main"),
    ):
        async with lifespan(FastAPI()):
            pass

    assert ensure_private.call_count >= 1
    warnings = [record.getMessage() for record in caplog.records]
    assert any("data_directory_not_private" in message for message in warnings), warnings
    assert any(problem in message for message in warnings), warnings
