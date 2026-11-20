"""PRS entrypoints use distinct synthetic sample, reference, PGS and gnomAD stores.

The frequencies and weights below are software fixtures, not scientific evidence.
Only score loaders are replaced; routing, scoring, calibration and storage are real.
"""

from __future__ import annotations

import importlib
import io
import json
import math
import multiprocessing
import re
import sqlite3
import tarfile
from contextlib import closing
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
from backend.db.sample_generations import (
    ANNOTATED_VARIANTS_GENERATION_KEY,
    IMPUTED_VARIANTS_GENERATION_KEY,
)
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    annotated_variants,
    annotation_state,
    clinvar_variants,
    database_versions,
    findings,
    imputed_variants,
    raw_variants,
    reference_metadata,
    samples,
)
from backend.services.route_scoring_state import read_scoring

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


@pytest.fixture
def positional_bmi(routing_env: _Environment, monkeypatch: pytest.MonkeyPatch) -> _Environment:
    from backend.analysis import metabolic_prs, pgs_bridge

    # Use the actual positional PGS loader for these two synthetic loci.
    monkeypatch.setattr(metabolic_prs, "build_trait_weight_set", pgs_bridge.build_trait_weight_set)
    with closing(
        sqlite3.connect(pgs_bridge.pgs_scores_db_path(routing_env.registry.settings.data_dir))
    ) as pgs:
        pgs.execute(
            """INSERT INTO pgs_score_metadata
               (pgs_id, pgs_name, trait_reported, genome_build, variants_number,
                weight_type, license, license_bundle_ok)
               VALUES ('PGS005198', 'Synthetic BMI', 'Synthetic', 'GRCh37', 2,
                       'beta', 'CC-BY-4.0', 1)"""
        )
        pgs.executemany(
            """INSERT INTO pgs_score_weights
               (pgs_id, rsid, chrom, pos, effect_allele, other_allele, effect_weight)
               VALUES ('PGS005198', NULL, ?, ?, ?, ?, 1.0)""",
            [("2", 500, "T", "C"), ("1", 999, "G", "A")],
        )
        pgs.commit()
    return routing_env


def _publish_bmi_imputation(env: _Environment, dosage: float) -> None:
    from backend.analysis.imputation_persist import persist_imputed_variants
    from backend.analysis.imputation_vcf import ImputedVariant

    imputed = ImputedVariant(
        chrom="1", pos=999, ref="A", alt="G", dr2=0.95, af=0.3, imputed=True, dosage=dosage
    )
    assert persist_imputed_variants(env.sample, [imputed]) == 1


def _add_typed_second_locus(env: _Environment, alleles: tuple[str, str | None]) -> None:
    ref, alt = alleles
    # Using the imputed dosage for this typed call would yield 2.25.
    _publish_bmi_imputation(env, 0.25)
    with env.sample.begin() as conn:
        conn.execute(
            annotated_variants.insert().values(
                rsid="SYN_TYPED_SECOND",
                chrom="1",
                pos=999,
                ref=ref,
                alt=alt,
                genotype="AG",
                gnomad_af_global=0.3,
                gnomad_af_eur=0.3,
            )
        )


def _run_positional_bmi(env: _Environment) -> dict:
    from backend.analysis import metabolic_prs

    # Observe the real scorer's result before the real writer applies the
    # separate rule for displaying a quantitative score without calibration.
    with patch.object(
        metabolic_prs, "store_prs_findings", wraps=metabolic_prs.store_prs_findings
    ) as writer:
        response = env.client.post("/api/analysis/metabolic/run?sample_id=1")
    assert response.status_code == 200, response.text
    assert response.json()["prs_traits_computed"] == 1
    writer.assert_called_once()
    (scored,) = writer.call_args.args[0]
    assert scored.raw_score == 3.0
    with env.sample.connect() as conn:
        stored_score, payload = conn.execute(
            sa.select(findings.c.prs_score, findings.c.detail_json).where(
                findings.c.module == "metabolic", findings.c.category == "prs"
            )
        ).one()
    result = json.loads(payload)
    assert result["pgs_id"] == "PGS005198"
    assert result["snps_used"] == result["snps_total"] == 2
    current = env.client.get("/api/analysis/metabolic/prs?sample_id=1")
    assert current.status_code == 200, current.text
    body = current.json()
    assert body["scoring_state"] == "current"
    assert body["total"] == 1
    (item,) = body["items"]
    for key in ("pgs_id", "calibrated", "snps_used", "snps_used_imputed", "percentile"):
        assert item[key] == result[key]
    receipt = read_scoring("metabolic", env.sample, env.reference)
    assert receipt.state == "current"
    assert receipt.recorded == body["scoring_generation"]
    assert isinstance(receipt.recorded, str) and re.fullmatch(r"[0-9a-f]{64}", receipt.recorded)
    return {**result, "stored_score": stored_score, "scoring_fingerprint": receipt.recorded}


def _update_second_frequency(env: _Environment, value: float) -> None:
    with closing(sqlite3.connect(env.registry.settings.gnomad_db_path)) as frequencies:
        updated = frequencies.execute(
            "UPDATE gnomad_af SET af_eur = ? WHERE chrom = '1' AND pos = 999", (value,)
        )
        assert updated.rowcount == 1
        frequencies.commit()


def _bmi_input_generations(sample: sa.Engine) -> dict[str, str]:
    with sample.connect() as conn:
        rows = conn.execute(
            sa.select(annotation_state.c.key, annotation_state.c.value).where(
                annotation_state.c.key.in_(
                    (ANNOTATED_VARIANTS_GENERATION_KEY, IMPUTED_VARIANTS_GENERATION_KEY)
                )
            )
        ).all()
    return {key: value for key, value in rows}


def _run_bmi_after_restart(
    data_dir: Path, expected_state: str | tuple[str, ...], refresh_annotation: bool = False
) -> dict:
    import _no_network
    from _isolated_data_home import isolate_data_home

    _no_network.install()
    _no_network.begin(enforce=True)
    try:
        with pytest.MonkeyPatch.context() as isolated:
            isolate_data_home(isolated, data_dir)
            from backend.db.connection import get_registry
            from backend.main import app
            from backend.services.reference_versions import read_current_reference_snapshot

            with TestClient(app) as client:
                registry = get_registry()
                assert registry.settings.data_dir == data_dir
                frequency_path = registry.settings.gnomad_db_path
                wal = frequency_path.with_name(frequency_path.name + "-wal")
                wal_size = wal.stat().st_size if wal.exists() else 0
                response = client.get("/api/analysis/metabolic/prs?sample_id=1")
                assert response.status_code == 200, response.text
                stored = response.json()
                assert stored["total"] == len(stored["items"])
                state = stored["scoring_state"]
                allowed = (expected_state,) if isinstance(expected_state, str) else expected_state
                assert state in allowed, f"Reopened state {state!r}, expected {expected_state!r}"
                sample = registry.get_sample_engine(data_dir / "samples" / "sample_1.db")
                receipt = read_scoring("metabolic", sample, registry.reference_engine)
                assert receipt.state == state
                copied_frequency = sa.select(annotated_variants.c.gnomad_af_eur).where(
                    annotated_variants.c.rsid == "SYN_TYPED_SECOND"
                )
                with sample.connect() as conn:
                    copied_before = conn.execute(copied_frequency).scalar_one_or_none()
                    persisted = (
                        conn.execute(
                            sa.select(findings.c.prs_score, findings.c.prs_percentile).where(
                                findings.c.module == "metabolic", findings.c.category == "prs"
                            )
                        )
                        .tuples()
                        .all()
                    )
                reopened = _Environment(
                    client, registry, sample, registry.reference_engine, "match"
                )
                inputs_before_rerun = _bmi_input_generations(sample)
                release = read_current_reference_snapshot(registry.reference_engine)["gnomad"]
                if refresh_annotation:
                    _annotate_bmi_with_controlled_ancestry(reopened, 2)
                result = _run_positional_bmi(reopened)
                with sample.connect() as conn:
                    copied_after = conn.execute(copied_frequency).scalar_one_or_none()
                frequency_uri = f"{frequency_path.as_uri()}?mode=ro"
                with closing(sqlite3.connect(frequency_uri, uri=True)) as db:
                    journal_mode = db.execute("PRAGMA journal_mode").fetchone()[0]
                return {
                    **result,
                    "state_before_rerun": state,
                    "recorded_before_rerun": receipt.recorded,
                    "copied_frequency_before_rerun": copied_before,
                    "copied_frequency_after_rerun": copied_after,
                    "visible_percentiles_before_rerun": [
                        (item["pgs_id"], item["percentile"]) for item in stored["items"]
                    ],
                    "scores_before_rerun": [tuple(row) for row in persisted],
                    "input_generations_before_rerun": inputs_before_rerun,
                    "frequency_release": release,
                    "frequency_journal_mode": journal_mode,
                    "frequency_wal_bytes": wal_size,
                }
    finally:
        attempted = _no_network.end()
        assert not attempted, f"Restart worker attempted network access: {attempted}"


def _prepare_bmi_restart(
    env: _Environment, *, typed_second: bool, complete_typed_alleles: bool = False
) -> tuple[Path, dict[str, str]]:
    from backend.analysis.imputation_persist import persist_imputed_variants

    assert not complete_typed_alleles or typed_second
    with env.reference.begin() as conn:
        conn.execute(
            database_versions.insert().values(
                db_name="gnomad", version="synthetic-frequency-release", genome_build="GRCh37"
            )
        )
    _seed_bmi_raw_calls(env, both_typed=typed_second, second_identity=complete_typed_alleles)
    _annotate_bmi_with_controlled_ancestry(env, 2 if typed_second else 1)
    if complete_typed_alleles:
        assert persist_imputed_variants(env.sample, []) == 0
        with env.sample.connect() as conn:
            assert conn.scalar(sa.select(sa.func.count()).select_from(imputed_variants)) == 0
    else:
        _publish_bmi_imputation(env, 0.25 if typed_second else 1.0)
    if typed_second:
        with env.sample.connect() as conn:
            c = annotated_variants.c
            typed = conn.execute(
                sa.select(c.genotype, c.ref, c.alt).where(c.rsid == "SYN_TYPED_SECOND")
            ).one()
        assert typed == (("AG", "A", "G") if complete_typed_alleles else ("AG", None, None))
    published = _bmi_input_generations(env.sample)
    assert set(published) == {ANNOTATED_VARIANTS_GENERATION_KEY, IMPUTED_VARIANTS_GENERATION_KEY}
    assert all(published.values())
    data_dir = env.registry.settings.data_dir
    env.client.close()
    env.registry.dispose_all()
    env.sample.dispose()
    env.reference.dispose()
    return data_dir, published


def _restart_bmi_pair(
    env: _Environment,
    *,
    typed_second: bool,
    frequency_changed: bool,
    complete_typed_alleles: bool = False,
) -> tuple[dict, dict, dict[str, str]]:
    data_dir, published = _prepare_bmi_restart(
        env, typed_second=typed_second, complete_typed_alleles=complete_typed_alleles
    )
    # Each run gets a separate interpreter, production startup and orderly shutdown.
    with multiprocessing.get_context("spawn").Pool(1, maxtasksperchild=1) as pool:
        original = pool.apply_async(_run_bmi_after_restart, (data_dir, "never")).get(timeout=60)
        if frequency_changed:
            _update_second_frequency(env, 0.6)
        expected_state = "stale" if frequency_changed else "current"
        repaired = pool.apply_async(
            _run_bmi_after_restart,
            (data_dir, expected_state, complete_typed_alleles and frequency_changed),
        ).get(timeout=60)
    assert original["recorded_before_rerun"] is None
    assert repaired["recorded_before_rerun"] == original["scoring_fingerprint"]
    return original, repaired, published


@pytest.mark.parametrize("routing_env", ["match"], indirect=True)
@pytest.mark.parametrize(
    ("typed_second", "complete_typed_alleles", "expected_imputed"),
    [
        pytest.param(False, False, 1, id="imputed"),
        pytest.param(True, False, 0, id="typed-incomplete"),
        pytest.param(True, True, 0, id="typed-complete"),
    ],
)
@pytest.mark.parametrize("frequency_changed", [True, False], ids=["changed", "unchanged"])
def test_positional_bmi_reopens_with_durable_scoring_state(
    positional_bmi: _Environment,
    typed_second: bool,
    complete_typed_alleles: bool,
    expected_imputed: int,
    frequency_changed: bool,
) -> None:
    original, repaired, published = _restart_bmi_pair(
        positional_bmi,
        typed_second=typed_second,
        frequency_changed=frequency_changed,
        complete_typed_alleles=complete_typed_alleles,
    )
    for run in (original, repaired):
        assert run["input_generations_before_rerun"] == published
        assert run["frequency_release"] == {
            "version": "synthetic-frequency-release",
            "genome_build": "GRCh37",
        }
        assert run["frequency_journal_mode"] == "wal"
        if not frequency_changed:
            assert run["frequency_wal_bytes"] == 0
        assert run["calibrated"] is True
        assert run["stored_score"] == 3.0
        assert run["snps_used_imputed"] == expected_imputed
    assert original["calibration_reference_std"] == pytest.approx(math.sqrt(0.9))
    assert repaired["calibration_reference_std"] == pytest.approx(
        math.sqrt(0.96 if frequency_changed else 0.9)
    )
    assert original["calibration_reference_mean"] == pytest.approx(1.4)
    assert repaired["calibration_reference_mean"] == pytest.approx(
        2.0 if frequency_changed else 1.4
    )
    assert original["percentile"] == 95.42
    assert repaired["percentile"] == (84.63 if frequency_changed else 95.42)
    if complete_typed_alleles:
        assert original["copied_frequency_before_rerun"] == 0.3
        assert repaired["copied_frequency_before_rerun"] == 0.3
        assert repaired["copied_frequency_after_rerun"] == (0.6 if frequency_changed else 0.3)
    if not frequency_changed:
        assert repaired["visible_percentiles_before_rerun"] == [("PGS005198", 95.42)]


class _StalePercentileDisplayed(AssertionError):
    """A stale first GET still displays the specific superseded fixture percentile."""


@pytest.mark.parametrize("routing_env", ["match"], indirect=True)
@pytest.mark.xfail(
    strict=True, raises=_StalePercentileDisplayed, reason="Approved withholding policy #2705"
)
@pytest.mark.parametrize("fully_typed", [False, True], ids=["imputed", "typed-complete"])
def test_stale_bmi_read_withholds_the_previous_percentile_before_rerun(
    positional_bmi: _Environment,
    fully_typed: bool,
) -> None:
    original, repaired, _ = _restart_bmi_pair(
        positional_bmi,
        typed_second=fully_typed,
        frequency_changed=True,
        complete_typed_alleles=fully_typed,
    )
    assert original["percentile"] == 95.42
    assert repaired["percentile"] == 84.63
    assert repaired["scores_before_rerun"] == [(3.0, 95.42)]
    visible = repaired["visible_percentiles_before_rerun"]
    if visible == [("PGS005198", 95.42)]:
        raise _StalePercentileDisplayed(
            "First stale GET displayed 95.42 before rerun yielded 84.63"
        )
    assert visible in ([], [("PGS005198", None)])


@pytest.fixture
def checkpointed_bmi(
    positional_bmi: _Environment,
) -> tuple[dict, dict, dict[str, str]]:
    data_dir, published = _prepare_bmi_restart(positional_bmi, typed_second=False)
    path = positional_bmi.registry.settings.gnomad_db_path
    wal = path.with_name(path.name + "-wal")
    frequency_query = "SELECT af_eur FROM gnomad_af WHERE chrom='1' AND pos=999"
    ctx = multiprocessing.get_context("spawn")
    with closing(sqlite3.connect(path)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        assert writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0, 0, 0)
        assert writer.execute(frequency_query).fetchall() == [(0.3,)]
        checkpointed_image = path.read_bytes()
        assert (
            writer.execute("UPDATE gnomad_af SET af_eur=0.6 WHERE chrom='1' AND pos=999").rowcount
            == 1
        )
        writer.commit()
        assert writer.execute(frequency_query).fetchall() == [(0.6,)]
        with ctx.Pool(1) as pool:
            before = pool.apply_async(_run_bmi_after_restart, (data_dir, "never")).get(timeout=60)
        assert before["scores_before_rerun"] == []
        assert before["frequency_wal_bytes"] > 0 and wal.stat().st_size > 0
        assert path.read_bytes() == checkpointed_image
        assert writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0, 0, 0)
    with ctx.Pool(1) as pool:
        after = pool.apply_async(_run_bmi_after_restart, (data_dir, ("current", "stale"))).get(
            timeout=60
        )
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as reader:
        assert reader.execute(frequency_query).fetchall() == [(0.6,)]
    assert after["frequency_wal_bytes"] == 0
    assert after["scores_before_rerun"] == [(3.0, 84.63)]
    assert before["recorded_before_rerun"] is None
    assert after["recorded_before_rerun"] == before["scoring_fingerprint"]
    return before, after, published


@pytest.mark.parametrize("routing_env", ["match"], indirect=True)
def test_positional_bmi_checkpoint_preserves_committed_frequency_and_score(
    checkpointed_bmi: tuple[dict, dict, dict[str, str]],
) -> None:
    before, after, published = checkpointed_bmi
    for run in (before, after):
        assert run["input_generations_before_rerun"] == published
        assert run["stored_score"] == 3.0
        assert run["calibration_reference_mean"] == pytest.approx(2.0)
        assert run["calibration_reference_std"] == pytest.approx(math.sqrt(0.96))
        assert run["percentile"] == 84.63


class _CheckpointInvalidatedReceipt(AssertionError):
    """Checkpointing the unchanged scoring input invalidated its persisted receipt."""


@pytest.mark.parametrize("routing_env", ["match"], indirect=True)
@pytest.mark.xfail(
    strict=True, raises=_CheckpointInvalidatedReceipt, reason="Content revision proposal #2705"
)
def test_positional_bmi_checkpoint_leaves_the_unchanged_result_current(
    checkpointed_bmi: tuple[dict, dict, dict[str, str]],
) -> None:
    before, after, published = checkpointed_bmi
    assert (
        before["input_generations_before_rerun"]
        == after["input_generations_before_rerun"]
        == published
    )
    assert before["percentile"] == after["percentile"] == 84.63
    if after["state_before_rerun"] == "stale":
        raise _CheckpointInvalidatedReceipt(
            "Unchanged 84.63 score reopened stale after checkpoint"
        )
    assert after["state_before_rerun"] == "current"
    assert after["visible_percentiles_before_rerun"] == [("PGS005198", 84.63)]


def _seed_bmi_raw_calls(env: _Environment, *, both_typed: bool, second_identity: bool) -> None:
    loci = [{"rsid": "SYN_TYPED", "chrom": "2", "pos": 500, "ref": "C", "alt": "T"}]
    calls = ["TT"]
    if both_typed:
        loci.append({"rsid": "SYN_TYPED_SECOND", "chrom": "1", "pos": 999, "ref": "A", "alt": "G"})
        calls.append("AG")
    with env.reference.begin() as conn:
        conn.execute(clinvar_variants.insert(), loci if second_identity else loci[:1])
    with env.sample.begin() as conn:
        conn.execute(
            raw_variants.insert(),
            [
                {
                    "rsid": locus["rsid"],
                    "chrom": locus["chrom"],
                    "pos": locus["pos"],
                    "genotype": call,
                }
                for locus, call in zip(loci, calls, strict=True)
            ],
        )
    with closing(sqlite3.connect(env.registry.settings.gnomad_db_path)) as frequencies:
        assert (
            frequencies.execute(
                "UPDATE gnomad_af SET rsid='SYN_TYPED_SECOND' WHERE chrom='1' AND pos=999"
            ).rowcount
            == 1
        )
        frequencies.execute(
            """INSERT INTO gnomad_af
               (chrom, pos, ref, alt, rsid, af_global, af_eur, homozygous_count)
               VALUES ('2', 500, 'C', 'T', 'SYN_TYPED', 0.4, 0.4, 0)"""
        )
        frequencies.commit()


def _annotate_bmi_with_controlled_ancestry(env: _Environment, expected_rows: int) -> str:
    from backend.analysis.ancestry import AncestryResult, store_ancestry_findings
    from backend.annotation.engine import run_annotation

    assert run_annotation(env.sample, env.registry).rows_written == expected_rows
    generation = _bmi_input_generations(env.sample)[ANNOTATED_VARIANTS_GENERATION_KEY]
    assert generation
    # Hold the calibration population fixed while testing frequency routing.
    # This is a controlled prerequisite, not ancestry inferred from two markers.
    result = AncestryResult(
        pc_scores=[0.0],
        top_population="EUR",
        population_distances={"EUR": 0.0},
        admixture_fractions={"EUR": 1.0},
        snps_used=expected_rows,
        snps_total=expected_rows,
        coverage_fraction=1.0,
        projection_time_ms=0.0,
        is_sufficient=True,
        inference_provenance={"source": "synthetic fixture", "annotation_generation": generation},
    )
    assert store_ancestry_findings(result, env.sample) == 3
    with env.sample.connect() as conn:
        detail = conn.execute(
            sa.select(findings.c.detail_json).where(
                findings.c.module == "ancestry", findings.c.category == "nnls_admixture"
            )
        ).scalar_one()
    assert json.loads(detail)["inference_provenance"] == result.inference_provenance, (
        "Ancestry result does not identify its annotation input"
    )
    return generation


@pytest.fixture
def two_typed_bmi(positional_bmi: _Environment) -> _Environment:
    _seed_bmi_raw_calls(positional_bmi, both_typed=True, second_identity=True)
    with positional_bmi.sample.begin() as conn:
        # Neither locus may obtain alleles or frequencies through imputation.
        assert conn.execute(imputed_variants.delete()).rowcount == 1
    return positional_bmi


@pytest.mark.parametrize("routing_env", ["match"], indirect=True)
@pytest.mark.parametrize(
    ("frequency", "mean", "variance", "percentile"),
    [(0.7, 2.2, 0.90, 80.05), (0.6, 2.0, 0.96, 84.63)],
)
def test_positional_bmi_refreshes_copied_frequencies_through_annotation(
    two_typed_bmi: _Environment, frequency: float, mean: float, variance: float, percentile: float
) -> None:
    env = two_typed_bmi

    def check_annotation(expected_frequency: float) -> str:
        generation = _annotate_bmi_with_controlled_ancestry(env, 2)
        with env.sample.connect() as conn:
            columns = annotated_variants.c
            rows = conn.execute(
                sa.select(columns.rsid, columns.ref, columns.alt, columns.gnomad_af_eur).order_by(
                    columns.rsid
                )
            ).all()
        assert rows == [
            ("SYN_TYPED", "C", "T", 0.4),
            ("SYN_TYPED_SECOND", "A", "G", expected_frequency),
        ]
        return generation

    previous_generation = check_annotation(0.3)
    original = _run_positional_bmi(env)
    _update_second_frequency(env, frequency)
    assert _route_state(env, "metabolic") == "stale"
    assert check_annotation(frequency) != previous_generation
    refreshed = _run_positional_bmi(env)
    assert original["snps_used_imputed"] == refreshed["snps_used_imputed"] == 0
    assert original["stored_score"] == refreshed["stored_score"] == 3.0
    assert original["calibration_reference_mean"] == pytest.approx(1.4)
    assert original["calibration_reference_std"] == pytest.approx(math.sqrt(0.9))
    assert refreshed["calibration_reference_mean"] == pytest.approx(mean)
    assert refreshed["calibration_reference_std"] == pytest.approx(math.sqrt(variance))
    assert original["percentile"] == 95.42
    assert refreshed["percentile"] == percentile


class _ObsoleteFrequencyPublished(AssertionError):
    """The scorer certified the fixture percentile for the previous copied frequency."""


@pytest.mark.parametrize("routing_env", ["match"], indirect=True)
@pytest.mark.xfail(strict=True, raises=_ObsoleteFrequencyPublished, reason="Issue #2746")
def test_scorer_only_bmi_repair_cannot_certify_obsolete_copied_frequency(
    two_typed_bmi: _Environment,
) -> None:
    env = two_typed_bmi
    _annotate_bmi_with_controlled_ancestry(env, 2)
    original = _run_positional_bmi(env)
    assert original["percentile"] == 95.42
    _update_second_frequency(env, 0.7)
    assert _route_state(env, "metabolic") == "stale"

    response = env.client.post("/api/analysis/metabolic/run?sample_id=1")
    assert response.status_code in (200, 409), response.text
    # Capture the writer's durable result before a GET or reannotation can change it.
    receipt = read_scoring("metabolic", env.sample, env.reference)
    with env.sample.connect() as conn:
        persisted = conn.execute(
            sa.select(
                findings.c.prs_score, findings.c.prs_percentile, findings.c.detail_json
            ).where(findings.c.module == "metabolic", findings.c.category == "prs")
        ).one_or_none()
    read = env.client.get("/api/analysis/metabolic/prs?sample_id=1")
    assert read.status_code == 200, read.text
    observed = read.json()
    _annotate_bmi_with_controlled_ancestry(env, 2)
    fresh = _run_positional_bmi(env)
    assert fresh["percentile"] == 80.05
    assert observed["total"] == len(observed["items"])
    if receipt.state == "stale":
        assert receipt.recorded == original["scoring_fingerprint"]
        assert observed["scoring_state"] == "stale"
        assert all(item["percentile"] is None for item in observed["items"])
        return
    assert receipt.state == "current"
    assert observed["scoring_state"] == "current" and response.status_code == 200
    assert receipt.recorded == observed["scoring_generation"]
    assert receipt.recorded != original["scoring_fingerprint"]
    assert persisted is not None
    score, stored_percentile, payload = persisted
    detail = json.loads(payload)
    assert score == fresh["stored_score"] == 3.0
    (item,) = observed["items"]
    assert item["pgs_id"] == fresh["pgs_id"] == "PGS005198"
    assert detail["pgs_id"] == "PGS005198"
    assert item["percentile"] == stored_percentile == detail["percentile"]
    if stored_percentile == 95.42:
        assert detail["calibration_reference_mean"] == pytest.approx(1.4)
        assert detail["calibration_reference_std"] == pytest.approx(math.sqrt(0.9))
        raise _ObsoleteFrequencyPublished("Scorer alone returned current at 95.42, fresh at 80.05")
    assert item["percentile"] == fresh["percentile"]
    assert detail["calibration_reference_mean"] == pytest.approx(
        fresh["calibration_reference_mean"]
    )
    assert detail["calibration_reference_std"] == pytest.approx(fresh["calibration_reference_std"])


@pytest.mark.parametrize("routing_env", ["match"], indirect=True)
@pytest.mark.parametrize("typed_alt", [None, "G"], ids=["incomplete", "complete"])
def test_typed_bmi_calibration_depends_on_the_source_of_its_alleles(
    positional_bmi: _Environment, typed_alt: str | None
) -> None:
    from backend.analysis.imputation_persist import persist_imputed_variants

    _add_typed_second_locus(positional_bmi, ("A", typed_alt))
    generation = sa.select(annotation_state.c.value).where(
        annotation_state.c.key == IMPUTED_VARIANTS_GENERATION_KEY
    )
    with positional_bmi.sample.connect() as conn:
        first_generation = conn.execute(generation).scalar_one()
    assert first_generation
    before = _run_positional_bmi(positional_bmi)
    assert before["calibrated"] is True
    assert before["snps_used_imputed"] == 0
    assert before["stored_score"] == 3.0
    assert before["percentile"] == 95.42

    # Keep the typed genotype, PGS weights and frequency row fixed.
    assert persist_imputed_variants(positional_bmi.sample, []) == 0
    with positional_bmi.sample.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(imputed_variants)) == 0
        next_generation = conn.execute(generation).scalar_one()
        assert next_generation and next_generation != first_generation
    if typed_alt is None:
        assert _route_state(positional_bmi, "metabolic") == "stale"
    after = _run_positional_bmi(positional_bmi)
    assert after["snps_used_imputed"] == 0
    if typed_alt is None:
        assert after["calibrated"] is False
        assert after["percentile"] is None
        assert after["stored_score"] is None
        assert after["calibration_reference_mean"] is None
    else:
        assert after["calibrated"] is True
        assert after["percentile"] == before["percentile"]
        assert after["stored_score"] == before["stored_score"]
        assert after["calibration_reference_mean"] == pytest.approx(1.4)


@pytest.mark.parametrize("routing_env", ["match"], indirect=True)
def test_positional_bmi_recovers_when_an_installed_store_gains_a_matching_row(
    positional_bmi: _Environment,
) -> None:
    path = positional_bmi.registry.settings.gnomad_db_path
    with closing(sqlite3.connect(path)) as frequencies:
        moved = frequencies.execute("UPDATE gnomad_af SET pos=1000 WHERE chrom='1' AND pos=999")
        assert moved.rowcount == 1
        frequencies.commit()
        missing = _run_positional_bmi(positional_bmi)
        assert missing["snps_used_imputed"] == 1
        assert missing["calibrated"] is False
        assert missing["stored_score"] is None
        assert missing["percentile"] is None
        restored = frequencies.execute("UPDATE gnomad_af SET pos=999 WHERE chrom='1' AND pos=1000")
        assert restored.rowcount == 1
        frequencies.commit()
    assert _route_state(positional_bmi, "metabolic") == "stale"
    repaired = _run_positional_bmi(positional_bmi)
    assert repaired["snps_used_imputed"] == 1
    assert repaired["calibrated"] is True
    assert repaired["stored_score"] == 3.0
    assert repaired["calibration_reference_mean"] == pytest.approx(1.4)
    assert repaired["percentile"] == 95.42


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
