"""Tests for the shared effective VEP bundle version resolver."""

from __future__ import annotations

import sqlalchemy as sa

from backend.db.tables import database_versions, reference_metadata
from backend.db.vep_version import (
    VERSIONLESS_VEP_BUNDLE_BASELINE,
    resolve_effective_vep_bundle_version,
)


def _reference_engine(version: str | None = None) -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    if version is not None:
        with engine.begin() as conn:
            conn.execute(
                database_versions.insert().values(
                    db_name="vep_bundle",
                    version=version,
                )
            )
    return engine


def _vep_engine(version: str | None = None) -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE bundle_metadata (key TEXT PRIMARY KEY, value TEXT)"))
        if version is not None:
            conn.execute(
                sa.text(
                    "INSERT INTO bundle_metadata (key, value) VALUES ('bundle_version', :version)"
                ),
                {"version": version},
            )
    return engine


def test_explicit_version_row_takes_precedence_over_embedded_metadata() -> None:
    reference_engine = _reference_engine("v2.0.0")
    vep_engine = _vep_engine("v9.0.0")

    assert resolve_effective_vep_bundle_version(reference_engine, vep_engine) == "v2.0.0"


def test_embedded_version_is_used_when_explicit_row_is_absent() -> None:
    reference_engine = _reference_engine()
    vep_engine = _vep_engine("v3.0.0")

    assert resolve_effective_vep_bundle_version(reference_engine, vep_engine) == "v3.0.0"
    with reference_engine.connect() as conn:
        row = conn.execute(
            sa.select(database_versions.c.version).where(
                database_versions.c.db_name == "vep_bundle"
            )
        ).fetchone()
    assert row is None


def test_versionless_bundle_uses_committed_fixture_baseline() -> None:
    reference_engine = _reference_engine()
    vep_engine = _vep_engine()

    assert (
        resolve_effective_vep_bundle_version(reference_engine, vep_engine)
        == VERSIONLESS_VEP_BUNDLE_BASELINE
        == "v1.0.0"
    )
