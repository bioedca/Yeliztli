"""Separate reviewed service diagnostics from ordinary stdout and stderr."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import TextIO

_console_fd: int | None = None


def _isolate_output() -> int:
    global _console_fd
    if _console_fd is None:
        # Python's dup is non-inheritable. Helpers inherit only the two discard
        # streams. Only the reload supervisor explicitly transfers the safe sink.
        _console_fd = os.dup(2)
        with open(os.devnull, "wb") as discard:
            os.dup2(discard.fileno(), 1)
            os.dup2(discard.fileno(), 2)
    return _console_fd


def managed_console() -> bool:
    return _console_fd is not None


def write_diagnostic(text: str, stream: TextIO) -> None:
    if _console_fd is None:
        stream.write(text)
    else:
        try:
            os.write(_console_fd, text.encode("utf-8"))
        except OSError:
            # A closed receiver must not trigger an unfiltered stderr fallback.
            pass


class _ReloadHandler:
    """The supervisor explicitly supplies the console descriptor to each new server."""

    def __call__(self):
        from backend.logging_config import _SafeHandler

        return _SafeHandler()

    def __reduce__(self):
        from multiprocessing.reduction import DupFd

        return _restore_reload_handler, (DupFd(_isolate_output()),)


def _restore_reload_handler(handle):
    global _console_fd
    _console_fd = handle.detach()
    os.set_inheritable(_console_fd, False)
    return _ReloadHandler()


class AccessStatus:
    """Report the HTTP status after the application starts its response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not managed_console():
            return await self.app(scope, receive, send)

        async def record_status(message):
            await send(message)
            if message["type"] == "http.response.start":
                status = message["status"]
                if type(status) is int and 100 <= status <= 599:
                    write_diagnostic(f"INFO HTTP response {status}\n", sys.stderr)

        return await self.app(scope, receive, record_status)


def _api() -> None:
    import uvicorn

    from backend.config import get_settings

    settings = get_settings()
    console_policy = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {"reviewed": {"()": _ReloadHandler()}},
        "loggers": {
            name: {"handlers": ["reviewed"], "level": "INFO", "propagate": False}
            for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
        },
    }
    uvicorn.run(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_config=console_policy,
        access_log=False,
    )


def _worker() -> None:
    from huey.consumer_options import ConsumerConfig, OptionParserHandler

    parser = OptionParserHandler().get_option_parser()
    options, positional = parser.parse_args()
    if positional or options.logfile:
        # The shipped consumer has one registered queue and one console route.
        raise ValueError
    config = ConsumerConfig(**{k: v for k, v in vars(options).items() if v is not None})
    config.validate()
    if sys.platform == "win32" and config.worker_type == "process":
        raise ValueError
    import logging

    from backend.tasks.huey_tasks import huey

    logging.getLogger("huey").setLevel(config.loglevel)
    huey.create_consumer(**config.values).run()


def _run(main: Callable[[], None]) -> int:
    try:
        _isolate_output()
        from backend.config import get_settings
        from backend.diagnostic_cutover import _CUTOVER_MESSAGE, CutoverPending, admit

        try:
            admit(get_settings().data_dir)
        except CutoverPending:
            write_diagnostic("ERROR " + _CUTOVER_MESSAGE + "\n", sys.stderr)
            return 1
        main()
    except KeyboardInterrupt:
        return 130
    except SystemExit as error:
        if error.code is None or (type(error.code) is int and error.code == 0):
            return 0
        write_diagnostic("ERROR The service could not start or complete its work.\n", sys.stderr)
        return error.code if type(error.code) is int and 1 <= error.code <= 125 else 1
    except BaseException:
        write_diagnostic("ERROR The service could not start or complete its work.\n", sys.stderr)
        return 1
    return 0


def run_api() -> int:
    return _run(_api)


def run_worker() -> int:
    return _run(_worker)
