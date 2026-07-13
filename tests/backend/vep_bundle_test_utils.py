"""Shared helpers for tests that exercise embedded VEP bundle metadata."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa


def seed_embedded_vep_bundle_version(db_path: Path, version: str) -> None:
    """Create embedded metadata without stamping the reference registry."""
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text("CREATE TABLE bundle_metadata (key TEXT PRIMARY KEY, value TEXT)")
            )
            conn.execute(
                sa.text(
                    "INSERT INTO bundle_metadata (key, value) VALUES ('bundle_version', :version)"
                ),
                {"version": version},
            )
    finally:
        engine.dispose()
