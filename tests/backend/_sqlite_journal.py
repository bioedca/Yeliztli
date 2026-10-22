"""Leave a genuine hot rollback journal beside a SQLite database (synthetic data).

A writer killed mid-transaction in rollback-journal mode is the only way to get
one SQLite will actually replay; a journal of arbitrary bytes has no valid
header and is simply discarded, so it cannot stand in for the hazard (#2585).
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path


def leave_hot_journal(db: Path) -> None:
    """Build a sample-shaped database at ``db``, then kill a writer mid-transaction."""
    with sqlite3.connect(db, isolation_level=None) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("CREATE TABLE raw_variants (rsid TEXT PRIMARY KEY, genotype TEXT)")
        conn.executemany(
            "INSERT INTO raw_variants VALUES (?, 'OLD')", ((f"old{i}",) for i in range(3000))
        )
    conn.close()
    writer = textwrap.dedent(
        """
        import os, sqlite3, sys
        conn = sqlite3.connect(sys.argv[1], isolation_level=None)
        conn.execute("PRAGMA cache_size=1")
        conn.execute("BEGIN")
        conn.executemany(
            "UPDATE raw_variants SET genotype = 'MID' WHERE rsid = ?",
            ((f"old{i}",) for i in range(3000)),
        )
        os._exit(9)
        """
    )
    subprocess.run([sys.executable, "-c", writer, str(db)], check=False, timeout=120)
    journal = Path(f"{db}-journal")
    assert journal.is_file() and journal.stat().st_size > 0, "writer left no hot journal"
