"""Resolve the GNU timeout contract without changing the system tool provider."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def resolve_gnu_timeout() -> dict[str, str]:
    for name in ("gnutimeout", "gtimeout", "timeout"):
        executable = shutil.which(name)
        if executable is None:
            continue
        try:
            result = subprocess.run(
                [executable, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                env={**os.environ, "LC_ALL": "C"},
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        lines = result.stdout.splitlines()
        if (
            result.returncode == 0
            and lines
            and re.fullmatch(r"(?:gnu|g)?timeout \(GNU coreutils\) \d[^\n]*", lines[0])
        ):
            # Preserve the selected alias: resolving a multicall symlink could
            # change which utility argv[0] selects.
            return {"executable": executable, "version": lines[0]}
    raise RuntimeError("GNU Coreutils timeout is required; checked gnutimeout, gtimeout, timeout.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        metadata = resolve_gnu_timeout()
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 2
    args.metadata_file.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(metadata["executable"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
