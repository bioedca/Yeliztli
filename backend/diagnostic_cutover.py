"""Startup admission for the application-managed diagnostic revision."""

from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path

from backend.db.tables import LOG_ENTRY_PRESENTATION_POLICY_VERSION

# A new launcher must refuse an incompatible installed writer/reader, even if
# somebody accidentally points its interpreter at a different release.
_LAUNCH_REVISION = 3
_RECEIPT = ".diagnostic-policy-v3"
_CUTOVER_MESSAGE = (
    "Diagnostic upgrade pending. Complete the operator cutover in the privacy guide."
)


class CutoverPending(Exception):
    """No released API or queue consumer should start against this installation yet."""


def _receipt_exists(path: Path) -> bool:
    try:
        attrs = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISREG(attrs.st_mode) and attrs.st_size == 0:
        return True
    raise CutoverPending


def admit(data_dir: Path, *, operator_verified: bool = False) -> None:
    """Record a fresh installation or an operator-confirmed, isolated upgrade.

    External admission is the operator's responsibility. This receipt recognizes
    the cutover at the new launchers; it cannot constrain an older executable.
    """
    if LOG_ENTRY_PRESENTATION_POLICY_VERSION != _LAUNCH_REVISION:
        raise CutoverPending
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    receipt = data_dir / _RECEIPT
    if _receipt_exists(receipt):
        return
    if not operator_verified:
        # Native installers can create these three empty directories first.
        for item in data_dir.iterdir():
            if item == receipt:
                continue
            if item.name in {"samples", "downloads", "logs"} and not item.is_symlink():
                if item.is_dir() and not any(item.iterdir()):
                    continue
            # A concurrent compatible service may have admitted the fresh
            # directory and already started creating its own files.
            if _receipt_exists(receipt):
                return
            raise CutoverPending
    try:
        descriptor = os.open(receipt, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        if not _receipt_exists(receipt):
            raise CutoverPending from None
    else:
        os.close(descriptor)


def main() -> None:
    parser = argparse.ArgumentParser(description="Confirm a completed diagnostic policy cutover.")
    parser.add_argument(
        "--confirm-isolated-upgrade",
        action="store_true",
        required=True,
        help="confirm that old services, browser sessions and rollback environments are isolated",
    )
    parser.parse_args()
    from backend.config import get_settings

    try:
        admit(get_settings().data_dir, operator_verified=True)
    except (CutoverPending, OSError):
        parser.exit(1, _CUTOVER_MESSAGE + "\n")


if __name__ == "__main__":
    main()
