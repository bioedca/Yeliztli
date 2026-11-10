"""PRS entrypoints use distinct synthetic sample, reference, PGS and gnomAD stores.

The frequencies and weights below are software fixtures, not scientific evidence.
Only score loaders are replaced; routing, scoring, calibration and storage are real.
"""

from __future__ import annotations

import importlib
import io
import json
import math
import sqlite3
import tarfile
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.analysis.prs import PRSSNPWeight, PRSWeightSet
from backend.annotation.gnomad import _INSERT_GNOMAD_SQL, _create_gnomad_table
from backend.annotation.pgs_catalog import pgs_score_metadata, pgs_score_weights
from backend.config import Settings
from backend.db.connection import DBRegistry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotated_variants,
    findings,
    imputed_variants,
    reference_metadata,
    samples,
)

MODULES = ("metabolic", "fh", "ebmd", "cancer", "traits")
ENTRYPOINTS = (*MODULES, "pipeline-cancer", "pipeline-traits")
SCENARIOS = (
    "match",
    "absent",
    "missing_row",
    "wrong_alleles",
    "missing_frequency",
    "no_imputation",
)


def _weight_set(trait: str, module: str) -> PRSWeightSet:
    return PRSWeightSet(
        name="Synthetic routing fixture",
        trait=trait,
        module=module,
        source_ancestry="EUR",
        source_study="Synthetic software fixture",
        source_pmid="",
        sample_size=0,
        weights=[
            PRSSNPWeight("SYN_TYPED", "T", 1.0, other_allele="C", chrom="2", pos=500),
            PRSSNPWeight("", "G", 1.0, other_allele="A", chrom="1", pos=999),
        ],
        reference_mean=0.0,
        reference_std=0.0,
        calibrated=False,
    )


@dataclass
class _Environment:
    client: TestClient
    registry: DBRegistry
    sample: sa.Engine
    reference: sa.Engine
    scenario: str

    def run(self, entrypoint: str, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
        module = entrypoint.removeprefix("pipeline-")
        if entrypoint.startswith("pipeline-"):
            from backend.analysis import run_all

            runner = getattr(run_all, f"_run_{module}")
            monkeypatch.setattr(run_all, "_get_modules", lambda: [(module, runner)])
            result = run_all.run_all_analyses(self.sample, self.registry, sample_id=1)
            assert isinstance(result[module], int), result
            assert result[module] > 0
        else:
            response = self.client.post(f"/api/analysis/{module}/run?sample_id=1")
            assert response.status_code == 200, response.text
            count_field = "prs_findings_count" if module == "cancer" else "findings_count"
            assert response.json()[count_field] > 0
        with self.sample.connect() as conn:
            rows = conn.execute(
                sa.select(findings.c.prs_score, findings.c.detail_json).where(
                    findings.c.module == module, findings.c.category == "prs"
                )
            ).all()
        assert rows, "The real entrypoint must persist a PRS finding"
        assert len(rows) == (2 if module == "metabolic" else 1)
        return [{**json.loads(row.detail_json), "stored_score": row.prs_score} for row in rows]


@pytest.fixture
def routing_env(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    scenario = request.param
    data_dir = tmp_path / "data"
    (data_dir / "samples").mkdir(parents=True)
    reference = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
    reference_metadata.create_all(reference)
    with reference.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1,
                name="Synthetic routing sample",
                db_path="samples/sample_1.db",
                file_format="23andme_v5",
                file_hash="synthetic-routing-fixture",
            )
        )
    assert not sa.inspect(reference).has_table("gnomad_af")
    assert sa.inspect(reference).has_table("samples")

    sample = sa.create_engine(f"sqlite:///{data_dir / 'samples/sample_1.db'}")
    create_sample_tables(sample)
    with sample.begin() as conn:
        conn.execute(
            findings.insert().values(
                module="ancestry",
                category="nnls_admixture",
                finding_text="Synthetic ancestry fixture",
                detail_json=json.dumps({"top_population": "EUR", "nnls_fractions": {"EUR": 1.0}}),
            )
        )
        conn.execute(
            annotated_variants.insert().values(
                rsid="SYN_TYPED",
                chrom="2",
                pos=500,
                ref="C",
                alt="T",
                genotype="TT",
                gnomad_af_global=0.4,
                gnomad_af_eur=0.4,
            )
        )
        if scenario != "no_imputation":
            conn.execute(
                imputed_variants.insert().values(
                    chrom="1", pos=999, ref="A", alt="G", dr2=0.95, af=0.3, dosage=1.0
                )
            )

    pgs = sa.create_engine(f"sqlite:///{data_dir / 'pgs_scores.db'}")
    pgs_score_metadata.create(pgs)
    pgs_score_weights.create(pgs)
    pgs.dispose()

    gnomad = None
    if scenario not in {"absent", "no_imputation"}:
        gnomad = sa.create_engine(f"sqlite:///{data_dir / 'gnomad_af.db'}")
        _create_gnomad_table(gnomad)
        assert not sa.inspect(gnomad).has_table("samples")
        if scenario != "missing_row":
            row = {
                "chrom": "1",
                "pos": 999,
                "ref": "A",
                "alt": "C" if scenario == "wrong_alleles" else "G",
                "rsid": None,
                "homozygous_count": 0,
            }
            row.update(
                {
                    f"{prefix}_{pop}": None
                    for prefix in ("af", "an")
                    for pop in ("global", "afr", "amr", "asj", "eas", "eur", "fin", "sas")
                }
            )
            row["af_global"] = 0.3
            row["af_eur"] = None if scenario == "missing_frequency" else 0.3
            with gnomad.begin() as conn:
                conn.execute(_INSERT_GNOMAD_SQL, row)

    settings = Settings(data_dir=data_dir)
    registry = DBRegistry(settings)
    monkeypatch.setattr("backend.api.dependencies.get_registry", lambda: registry)
    monkeypatch.setattr("backend.services.staleness.get_registry", lambda: registry)
    app = FastAPI()
    for module in MODULES:
        routes = importlib.import_module(f"backend.api.routes.{module}")
        monkeypatch.setattr(routes, "get_registry", lambda: registry)
        app.include_router(routes.router, prefix="/api")

    for module, target in (("metabolic", "metabolic_prs"), ("fh", "fh"), ("ebmd", "ebmd_prs")):
        scorer = importlib.import_module(f"backend.analysis.{target}")
        monkeypatch.setattr(
            scorer,
            "build_trait_weight_set",
            lambda _engine, trait, *_args, _module=module, **_kwargs: _weight_set(trait, _module),
        )
    monkeypatch.setattr(
        "backend.analysis.cancer_prs.load_cancer_prs_weights",
        lambda: [_weight_set("synthetic_trait", "cancer")],
    )
    monkeypatch.setattr(
        "backend.analysis.traits._load_prs_weight_sets",
        lambda _panel: [_weight_set("synthetic_trait", "traits")],
    )
    try:
        with (
            patch("backend.config.get_settings", return_value=settings),
            TestClient(app) as client,
        ):
            yield _Environment(client, registry, sample, reference, scenario)
    finally:
        registry.dispose_all()
        sample.dispose()
        reference.dispose()
        if gnomad is not None:
            gnomad.dispose()


@pytest.mark.parametrize("routing_env", SCENARIOS, indirect=True)
@pytest.mark.parametrize("entrypoint", ENTRYPOINTS)
def test_prs_entrypoints_use_the_standalone_frequency_store(
    routing_env: _Environment, entrypoint: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    results = routing_env.run(entrypoint, monkeypatch)
    assert not sa.inspect(routing_env.reference).has_table("gnomad_af")
    if routing_env.scenario in {"absent", "no_imputation"}:
        assert not routing_env.registry.settings.gnomad_db_path.exists()
    for result in results:
        typed_only = routing_env.scenario == "no_imputation"
        assert result["snps_used"] == (1 if typed_only else 2)
        assert result["snps_used_imputed"] == (0 if typed_only else 1)
        assert result["snps_total"] == 2
        if routing_env.scenario in {"match", "no_imputation"}:
            assert result["calibrated"] is True
            assert result["is_sufficient"] is True
            assert result["stored_score"] == (2.0 if typed_only else 3.0)
            assert result["calibration_method"] == "ancestry_continuous"
            assert result["calibration_variants_used"] == (1 if typed_only else 2)
            assert result["calibration_variants_total"] == (1 if typed_only else 2)
            assert result["calibration_reference_mean"] == pytest.approx(
                0.8 if typed_only else 1.4
            )
            assert result["calibration_reference_std"] == pytest.approx(
                math.sqrt(0.48 if typed_only else 0.9), abs=1e-6
            )
            assert result["percentile"] is not None
            assert 0 < result["percentile"] < 100
        else:
            assert result["calibrated"] is False
            assert result["calibration_method"] is None
            assert result["calibration_reference_mean"] is None
            assert result["calibration_reference_std"] is None
            assert result["stored_score"] is None
            assert result["percentile"] is None
            assert result["z_score"] is None


@pytest.mark.parametrize("routing_env", ["no_imputation"], indirect=True)
@pytest.mark.parametrize("entrypoint", ENTRYPOINTS)
@pytest.mark.parametrize("missing_alleles", ["ref", "alt", "both", "neither"])
def test_typed_contributions_need_complete_alleles_when_gnomad_is_absent(
    routing_env: _Environment,
    entrypoint: str,
    missing_alleles: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with routing_env.sample.begin() as conn:
        conn.execute(
            annotated_variants.insert().values(
                rsid="SYN_INCOMPLETE_TYPED",
                chrom="1",
                pos=999,
                ref=None if missing_alleles in {"ref", "both"} else "A",
                alt=None if missing_alleles in {"alt", "both"} else "G",
                genotype="GG",
                gnomad_af_global=0.3,
                gnomad_af_eur=0.3,
            )
        )
    results = routing_env.run(entrypoint, monkeypatch)
    assert not routing_env.registry.settings.gnomad_db_path.exists()
    for result in results:
        assert result["snps_used"] == result["snps_total"] == 2
        assert result["snps_used_imputed"] == 0
        if missing_alleles == "neither":
            assert result["calibrated"] is True
            assert result["stored_score"] == 4.0
            assert result["calibration_variants_used"] == 2
            assert result["calibration_reference_mean"] == pytest.approx(1.4)
            assert result["calibration_reference_std"] == pytest.approx(math.sqrt(0.9), abs=1e-6)
            assert result["percentile"] is not None
        else:
            assert result["calibrated"] is False
            assert result["calibration_method"] is None
            assert result["calibration_reference_mean"] is None
            assert result["calibration_reference_std"] is None
            assert result["stored_score"] is None
            assert result["percentile"] is None
            assert result["z_score"] is None


@pytest.mark.parametrize("routing_env", ["absent"], indirect=True)
@pytest.mark.parametrize("entrypoint", ENTRYPOINTS)
def test_an_unreadable_installed_store_fails_without_persisting_a_prs(
    routing_env: _Environment, entrypoint: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    routing_env.registry.settings.gnomad_db_path.write_bytes(b"synthetic invalid SQLite store")
    module = entrypoint.removeprefix("pipeline-")
    if entrypoint.startswith("pipeline-"):
        from backend.analysis import run_all

        runner = getattr(run_all, f"_run_{module}")
        monkeypatch.setattr(run_all, "_get_modules", lambda: [(module, runner)])
        result = run_all.run_all_analyses(routing_env.sample, routing_env.registry, sample_id=1)
        assert result[module] == "error"
    else:
        with pytest.raises(sa.exc.DatabaseError, match="file is not a database"):
            routing_env.client.post(f"/api/analysis/{module}/run?sample_id=1")
    with routing_env.sample.connect() as conn:
        assert (
            conn.scalar(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == module, findings.c.category == "prs")
            )
            == 0
        )


@pytest.mark.parametrize("routing_env", ["absent"], indirect=True)
def test_a_frequency_store_permission_error_is_not_treated_as_absence(
    routing_env: _Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.analysis.prs_calibration import get_optional_gnomad_engine

    original_stat = Path.stat
    frequency_path = routing_env.registry.settings.gnomad_db_path

    def denied_stat(path: Path, *args, **kwargs):
        if path == frequency_path:
            raise PermissionError("synthetic denied frequency store")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", denied_stat)
    with pytest.raises(PermissionError, match="synthetic denied frequency store"):
        get_optional_gnomad_engine(routing_env.registry)


def _route_state(env: _Environment, module: str) -> str:
    endpoint = "assessment" if module == "fh" else "prs"
    response = env.client.get(f"/api/analysis/{module}/{endpoint}?sample_id=1")
    assert response.status_code == 200, response.text
    return response.json()["scoring_state"]


@pytest.mark.parametrize("routing_env", ["match"], indirect=True)
@pytest.mark.parametrize("module", ["metabolic", "fh", "ebmd"])
def test_restored_frequency_store_invalidates_scores_without_a_version_change(
    routing_env: _Environment, module: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from backend.api.routes import setup
    from backend.services.reference_versions import read_current_reference_snapshot

    before = routing_env.run(module, monkeypatch)
    # Re-reading unchanged inputs must not spuriously invalidate a successful run.
    assert _route_state(routing_env, module) == "current"
    assert _route_state(routing_env, module) == "current"
    versions = read_current_reference_snapshot(routing_env.reference)
    replacement = tmp_path / "replacement.db"
    source = sqlite3.connect(routing_env.registry.settings.gnomad_db_path)
    destination = sqlite3.connect(replacement)
    try:
        source.backup(destination)
        destination.execute("UPDATE gnomad_af SET af_eur = 0.7")
        destination.commit()
    finally:
        destination.close()
        source.close()
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tf:
        directory = tarfile.TarInfo("samples")
        directory.type = tarfile.DIRTYPE
        tf.addfile(directory)
        tf.add(replacement, arcname="gnomad_af.db")
    monkeypatch.setattr(setup, "get_settings", lambda: routing_env.registry.settings)
    monkeypatch.setattr(setup, "get_registry", lambda: routing_env.registry)
    routing_env.client.app.include_router(setup.router, prefix="/api")
    response = routing_env.client.post(
        "/api/setup/import-backup",
        files={"file": ("synthetic.tgz", archive.getvalue(), "application/gzip")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    assert read_current_reference_snapshot(routing_env.reference) == versions
    assert _route_state(routing_env, module) == "stale"
    after = routing_env.run(module, monkeypatch)
    assert len(before) == len(after) > 0
    for old, new in zip(before, after, strict=True):
        assert new["stored_score"] == old["stored_score"] == 3.0
        assert old["calibration_reference_mean"] == pytest.approx(1.4)
        assert new["calibration_reference_mean"] == pytest.approx(2.2)
        assert new["percentile"] != old["percentile"]
    assert _route_state(routing_env, module) == "current"


@pytest.mark.parametrize("routing_env", ["match"], indirect=True)
@pytest.mark.parametrize("module", ["metabolic", "fh", "ebmd"])
def test_frequency_removal_and_installation_invalidate_route_scores(
    routing_env: _Environment, module: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from backend.db.build_guard import database_access

    routing_env.run(module, monkeypatch)
    assert _route_state(routing_env, module) == "current"
    path = routing_env.registry.settings.gnomad_db_path
    saved = tmp_path / "saved-frequency.db"
    with database_access("gnomad", path.parent, publish=True):
        path.replace(saved)
    assert _route_state(routing_env, module) == "stale"
    for result in routing_env.run(module, monkeypatch):
        assert result["calibrated"] is False
        assert result["stored_score"] is None
    assert _route_state(routing_env, module) == "current"
    assert not path.exists()
    with database_access("gnomad", path.parent, publish=True):
        saved.replace(path)
    assert _route_state(routing_env, module) == "stale"
    for result in routing_env.run(module, monkeypatch):
        assert result["calibrated"] is True
        assert result["calibration_reference_mean"] == pytest.approx(1.4)
    assert _route_state(routing_env, module) == "current"


@pytest.mark.parametrize("routing_env", ["match"], indirect=True)
@pytest.mark.parametrize("module", ["metabolic", "fh", "ebmd"])
def test_frequency_wal_changes_invalidate_route_scores(
    routing_env: _Environment, module: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = routing_env.registry.settings.gnomad_db_path
    writer = sqlite3.connect(path)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        routing_env.run(module, monkeypatch)
        assert _route_state(routing_env, module) == "current"
        before = path.stat()
        writer.execute("UPDATE gnomad_af SET af_eur = 0.7")
        writer.commit()
        assert path.stat().st_mtime_ns == before.st_mtime_ns
        assert path.with_name(path.name + "-wal").stat().st_size > 0
        assert _route_state(routing_env, module) == "stale"
        for result in routing_env.run(module, monkeypatch):
            assert result["calibration_reference_mean"] == pytest.approx(2.2)
        assert _route_state(routing_env, module) == "current"
    finally:
        writer.close()


@pytest.mark.parametrize("routing_env", ["match"], indirect=True)
@pytest.mark.parametrize("entrypoint", ENTRYPOINTS)
def test_cleanup_after_engine_selection_never_recreates_the_frequency_store(
    routing_env: _Environment, entrypoint: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.analysis import prs_calibration
    from backend.db.build_guard import cross_process_build_claim
    from backend.db.database_registry import DATABASES
    from backend.db.db_health import clean_database_artifacts
    from backend.db.standalone import standalone_cleanup

    settings = routing_env.registry.settings
    original = prs_calibration.get_optional_gnomad_engine
    selected = []

    def select_then_clean(registry):
        engine = original(registry)
        if not selected:
            assert engine is not None
            selected.append(engine)
            info = DATABASES["gnomad"]
            with cross_process_build_claim("gnomad", settings.data_dir) as acquired:
                assert acquired
                with standalone_cleanup(info, settings):
                    removed = clean_database_artifacts(info, settings, registry.reference_engine)
            assert str(settings.gnomad_db_path) in removed["removed"]
            assert not settings.gnomad_db_path.exists()
        return engine

    monkeypatch.setattr(prs_calibration, "get_optional_gnomad_engine", select_then_clean)
    for result in routing_env.run(entrypoint, monkeypatch):
        assert result["calibrated"] is False
        assert result["stored_score"] is None
        assert result["percentile"] is None
    assert len(selected) == 1
    assert not settings.gnomad_db_path.exists()


@pytest.mark.parametrize("directory", ["ordinary", "space ? # %"])
def test_frequency_reader_is_read_only_and_preserves_literal_paths(
    tmp_path: Path, directory: str
) -> None:
    from backend.analysis.prs_calibration import get_optional_gnomad_engine

    settings = Settings(data_dir=tmp_path / directory)
    settings.data_dir.mkdir()
    with sqlite3.connect(settings.gnomad_db_path) as conn:
        conn.execute("CREATE TABLE marker (value TEXT)")
        conn.execute("INSERT INTO marker VALUES ('synthetic')")
    conn.close()
    before = settings.gnomad_db_path.read_bytes()
    registry = DBRegistry(settings)
    engine = get_optional_gnomad_engine(registry)
    assert engine is not None
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT value FROM marker").scalar_one() == "synthetic"
            with pytest.raises(sa.exc.OperationalError, match="readonly"):
                conn.exec_driver_sql("DELETE FROM marker")
        assert settings.gnomad_db_path.read_bytes() == before
    finally:
        engine.dispose()
        registry.dispose_all()


@pytest.mark.parametrize("routing_env", ["match"], indirect=True)
def test_frequency_reader_checks_existence_under_the_publication_lease(
    routing_env: _Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.analysis.prs_calibration import get_optional_gnomad_engine
    from backend.db.build_guard import database_access

    path = routing_env.registry.settings.gnomad_db_path
    engine = get_optional_gnomad_engine(routing_env.registry)
    assert engine is not None
    original_stat = Path.stat
    checked = []

    def check_lease(current: Path, *args, **kwargs):
        if current == path:
            with pytest.raises(TimeoutError):
                with database_access("gnomad", path.parent, publish=True, timeout=0):
                    pytest.fail("A publisher entered between existence check and connection")
            checked.append(True)
        return original_stat(current, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", check_lease)
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT af_eur FROM gnomad_af").scalar_one() == 0.3
            with pytest.raises(TimeoutError):
                with database_access("gnomad", path.parent, publish=True, timeout=0):
                    pytest.fail("The reader released its lease before connection close")
        assert checked
        with database_access("gnomad", path.parent, publish=True, timeout=0):
            pass
    finally:
        engine.dispose()


@pytest.mark.parametrize("routing_env", ["match"], indirect=True)
def test_external_removal_during_open_cannot_create_a_replacement_database(
    routing_env: _Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.analysis.prs_calibration import get_optional_gnomad_engine

    path = routing_env.registry.settings.gnomad_db_path
    engine = get_optional_gnomad_engine(routing_env.registry)
    assert engine is not None
    original_stat = Path.stat
    removed = []

    def remove_after_stat(current: Path, *args, **kwargs):
        result = original_stat(current, *args, **kwargs)
        if current == path and not removed:
            # An external deletion ignoring the cooperative lease must still
            # never turn this reader into a database creator.
            path.unlink()
            removed.append(True)
        return result

    monkeypatch.setattr(Path, "stat", remove_after_stat)
    try:
        with pytest.raises(sa.exc.OperationalError, match="unable to open database"):
            with engine.connect():
                pytest.fail("Opening a removed store unexpectedly succeeded")
        assert removed == [True]
        assert not path.exists()
    finally:
        engine.dispose()
