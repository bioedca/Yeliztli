"""Route-only scores record what they were computed from and report when it changed (#2118).

Metabolic, FH and eBMD are scored only by ``POST /run``, which takes minutes on a
full array, and their pages used to re-score on every visit. Each GET now
reports ``scoring_state``: ``never`` before a successful run, ``current`` while
every input matches the recorded fingerprint, and ``stale`` once one changes.
The page re-scores only for ``never`` and ``stale``.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.analysis.imputation_persist import persist_imputed_variants
from backend.annotation.engine import _reset_staging_table, _swap_staging_into_place
from backend.annotation.pgs_catalog import pgs_score_metadata, pgs_score_weights
from backend.config import Settings
from backend.db.connection import DBRegistry, reset_registry
from backend.db.sample_generations import upsert_state
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import database_versions, findings, reference_metadata, samples
from backend.services import route_scoring_state

MODULES = {
    "metabolic": "/api/analysis/metabolic/prs",
    "fh": "/api/analysis/fh/assessment",
    "ebmd": "/api/analysis/ebmd/prs",
}


class _Env:
    def __init__(self, client: TestClient, data_dir: Path) -> None:
        self.client = client
        self.data_dir = data_dir
        self.sample_engine = sa.create_engine(f"sqlite:///{data_dir / 'samples' / 'sample_1.db'}")
        self.reference_engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")

    def state(self, module: str) -> str:
        resp = self.client.get(f"{MODULES[module]}?sample_id=1")
        assert resp.status_code == 200, resp.text
        return resp.json()["scoring_state"]

    def run(self, module: str) -> int:
        return self.client.post(f"/api/analysis/{module}/run?sample_id=1").status_code

    def add_finding(self, **values: object) -> None:
        row = {"finding_text": "x", **values}
        with self.sample_engine.begin() as conn:
            conn.execute(sa.insert(findings), [row])


@pytest.fixture()
def env(tmp_path: Path) -> Generator[_Env, None, None]:
    from backend.api.routes.ebmd import router as ebmd_router
    from backend.api.routes.fh import router as fh_router
    from backend.api.routes.metabolic import router as metabolic_router

    data_dir = tmp_path / "data"
    (data_dir / "samples").mkdir(parents=True)
    ref_engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        conn.execute(
            sa.insert(samples),
            [
                {
                    "name": "test_sample",
                    "db_path": "samples/sample_1.db",
                    "file_format": "23andme_v5",
                    "file_hash": "abc123",
                }
            ],
        )
    ref_engine.dispose()
    sample_engine = sa.create_engine(f"sqlite:///{data_dir / 'samples' / 'sample_1.db'}")
    create_sample_tables(sample_engine)
    sample_engine.dispose()

    settings = Settings(data_dir=data_dir)
    reset_registry()
    registry = DBRegistry(settings)
    patchers = [
        patch(f"backend.api.routes.{name}.get_registry", return_value=registry) for name in MODULES
    ] + [
        patch("backend.api.dependencies.get_registry", return_value=registry),
        patch("backend.services.staleness.get_registry", return_value=registry),
        # The scorers and the fingerprint both locate pgs_scores.db here.
        patch("backend.config.get_settings", return_value=settings),
    ]
    for p in patchers:
        p.start()
    app = FastAPI()
    for router in (metabolic_router, fh_router, ebmd_router):
        app.include_router(router, prefix="/api")
    environment = _Env(TestClient(app, raise_server_exceptions=False), data_dir)
    try:
        yield environment
    finally:
        environment.sample_engine.dispose()
        environment.reference_engine.dispose()
        for p in reversed(patchers):
            p.stop()
        registry.dispose_all()
        reset_registry()


@pytest.mark.parametrize("module", sorted(MODULES))
def test_a_successful_run_makes_the_score_current(env: _Env, module: str) -> None:
    assert env.state(module) == "never"

    assert env.run(module) == 200

    assert env.state(module) == "current"
    # Reading the state is not itself an input change.
    assert env.state(module) == "current"


def _replace_annotated_variants(env: _Env) -> None:
    _reset_staging_table(env.sample_engine)
    _swap_staging_into_place(env.sample_engine)


def _replace_imputed_variants(env: _Env) -> None:
    persist_imputed_variants(env.sample_engine, [])


def _rerun_ancestry(env: _Env) -> None:
    env.add_finding(
        module="ancestry",
        category="nnls_admixture",
        detail_json=json.dumps({"top_population": "EUR", "admixture_fractions": {"EUR": 0.9}}),
    )


def _add_monogenic_variant(env: _Env) -> None:
    env.add_finding(
        module="cardiovascular",
        category="monogenic_variant",
        gene_symbol="LDLR",
        zygosity="het",
    )


def _install_pgs_scores(env: _Env) -> None:
    engine = sa.create_engine(f"sqlite:///{env.data_dir / 'pgs_scores.db'}")
    pgs_score_metadata.metadata.create_all(engine)
    engine.dispose()


def _update_a_reference_database(env: _Env) -> None:
    with env.reference_engine.begin() as conn:
        conn.execute(sa.insert(database_versions), [{"db_name": "gnomad", "version": "2.1.1"}])


INPUT_CHANGES = {
    "annotated variants replaced": _replace_annotated_variants,
    "imputed variants replaced": _replace_imputed_variants,
    "ancestry re-run": _rerun_ancestry,
    "monogenic variant found": _add_monogenic_variant,
    "PGS score database installed": _install_pgs_scores,
    "reference database updated": _update_a_reference_database,
}


@pytest.mark.parametrize("change", sorted(INPUT_CHANGES))
@pytest.mark.parametrize("module", sorted(MODULES))
def test_a_changed_input_makes_the_score_stale_until_rerun(
    env: _Env, module: str, change: str
) -> None:
    assert env.run(module) == 200
    assert env.state(module) == "current"

    INPUT_CHANGES[change](env)

    assert env.state(module) == "stale"
    assert env.run(module) == 200
    assert env.state(module) == "current"


def test_a_reloaded_pgs_score_database_makes_the_score_stale(env: _Env) -> None:
    _install_pgs_scores(env)
    assert env.run("ebmd") == 200
    assert env.state("ebmd") == "current"

    # A reload can change weights without changing their metadata rows.
    engine = sa.create_engine(f"sqlite:///{env.data_dir / 'pgs_scores.db'}")
    with engine.begin() as conn:
        conn.execute(
            sa.insert(pgs_score_weights),
            [
                {
                    "pgs_id": "PGS000657",
                    "chrom": "1",
                    "pos": 1,
                    "effect_allele": "A",
                    "effect_weight": 0.1,
                }
            ],
        )
    engine.dispose()

    assert env.state("ebmd") == "stale"


@pytest.mark.parametrize(
    "patched",
    [
        ("app_version", lambda: "999.0.0"),
        ("_code_digest", lambda: "different-code"),
    ],
)
def test_an_app_or_scoring_code_change_makes_the_score_stale(
    env: _Env, patched: tuple[str, object]
) -> None:
    assert env.run("ebmd") == 200
    assert env.state("ebmd") == "current"

    name, replacement = patched
    with patch.object(route_scoring_state, name, replacement):
        assert env.state("ebmd") == "stale"


def _record_finding_diff(env: _Env) -> None:
    with env.sample_engine.begin() as conn:
        upsert_state(conn, "last_finding_diff_json", json.dumps({"generated_at": "now"}))


def _flip_cyp2c9_marker(env: _Env) -> None:
    with env.sample_engine.begin() as conn:
        upsert_state(conn, "cyp2c9_phenytoin_reanalysis_required_json", '{"state": "prompted"}')


def _add_unrelated_finding(env: _Env) -> None:
    env.add_finding(module="traits", category="trait", rsid="rs1")


def _rerender_svgs(env: _Env) -> None:
    with env.sample_engine.begin() as conn:
        conn.execute(sa.update(findings).values(svg_path="/tmp/new.svg"))


def _rewrite_ancestry_identically(env: _Env) -> None:
    # Re-running ancestry deletes and re-inserts its rows: new ids and
    # timestamps, same content.
    with env.sample_engine.begin() as conn:
        rows = [
            {k: v for k, v in row._mapping.items() if k not in ("id", "created_at")}
            for row in conn.execute(sa.select(findings).where(findings.c.module == "ancestry"))
        ]
        assert rows, "the fixture's ancestry finding should exist"
        conn.execute(sa.delete(findings).where(findings.c.module == "ancestry"))
        conn.execute(sa.insert(findings), rows)


def _score_another_module(env: _Env) -> None:
    assert env.run("fh") == 200


def _empty_pgs_wal(env: _Env) -> None:
    (env.data_dir / "pgs_scores.db-wal").write_bytes(b"")


UNRELATED_CHANGES = {
    "finding diff recorded": _record_finding_diff,
    "CYP2C9 reanalysis marker flipped": _flip_cyp2c9_marker,
    "another module's finding": _add_unrelated_finding,
    "finding SVGs re-rendered": _rerender_svgs,
    "ancestry rewritten with identical content": _rewrite_ancestry_identically,
    "another module scored": _score_another_module,
    "empty PGS WAL file": _empty_pgs_wal,
}


@pytest.mark.parametrize("change", sorted(UNRELATED_CHANGES))
def test_an_unrelated_change_keeps_the_score_current(env: _Env, change: str) -> None:
    _rerun_ancestry(env)
    assert env.run("ebmd") == 200
    assert env.state("ebmd") == "current"

    UNRELATED_CHANGES[change](env)

    assert env.state("ebmd") == "current"


def test_an_input_that_changes_during_the_run_leaves_the_score_stale(env: _Env) -> None:
    from backend.analysis import ebmd_prs

    real_score = ebmd_prs.score_ebmd_prs

    def score_while_ancestry_reruns(*args, **kwargs):
        result = real_score(*args, **kwargs)
        _rerun_ancestry(env)
        return result

    with patch.object(ebmd_prs, "score_ebmd_prs", score_while_ancestry_reruns):
        assert env.run("ebmd") == 200

    assert env.state("ebmd") == "stale"


def test_a_failed_run_records_nothing(env: _Env) -> None:
    from backend.analysis import ebmd_prs

    with patch.object(ebmd_prs, "store_ebmd_findings", side_effect=RuntimeError("disk full")):
        assert env.run("ebmd") == 500

    assert env.state("ebmd") == "never"


def test_an_unreadable_record_is_stale_not_current(env: _Env) -> None:
    with env.sample_engine.begin() as conn:
        upsert_state(conn, "route_scoring_fingerprint:ebmd", "not json")

    assert env.state("ebmd") == "stale"


def _generation(engine: sa.Engine, key: str) -> str | None:
    with engine.connect() as conn:
        return route_scoring_state._state_value(conn, key)


def test_replacing_annotated_variants_writes_a_new_generation(env: _Env) -> None:
    key = "annotated_variants_generation"
    assert _generation(env.sample_engine, key) is None

    _replace_annotated_variants(env)
    first = _generation(env.sample_engine, key)
    _replace_annotated_variants(env)

    assert first is not None
    assert _generation(env.sample_engine, key) not in (None, first)


def test_persisting_imputed_variants_writes_a_new_generation(env: _Env) -> None:
    key = "imputed_variants_generation"
    assert _generation(env.sample_engine, key) is None

    _replace_imputed_variants(env)
    first = _generation(env.sample_engine, key)
    _replace_imputed_variants(env)

    assert first is not None
    assert _generation(env.sample_engine, key) not in (None, first)
