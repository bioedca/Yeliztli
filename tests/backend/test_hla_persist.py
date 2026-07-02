"""Predict + persist HIBAG HLA calls (Wave D glue).

Pins the idempotent ``hla_calls`` persistence (delete+insert, low_confidence
mapping) and the graceful predict-persist driver: ``unavailable`` when the runtime
/ ancestry model is absent, ``no_input`` when the sample has no HLA-region SNP,
``failed`` when the HIBAG run errors (table left untouched), and ``ok`` when a
(mocked) run produces calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.hibag_runner import HibagResult, HLACall
from backend.analysis.hla_persist import (
    STATUS_FAILED,
    STATUS_NO_INPUT,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    persist_hla_calls,
    predict_and_persist_hla_calls,
)
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import annotated_variants, hla_calls


@pytest.fixture
def sample_engine() -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    return engine


def _insert_variants(engine: sa.Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(annotated_variants.insert(), rows)


def _row(rsid, chrom, pos, ref, alt, zyg) -> dict:
    return {
        "rsid": rsid,
        "chrom": chrom,
        "pos": pos,
        "ref": ref,
        "alt": alt,
        "zygosity": zyg,
        "genotype": "",
    }


def _call(locus, a1, a2, prob, low_conf, matching=0.9) -> HLACall:
    return HLACall(
        locus=locus,
        sample_id="S",
        allele1=a1,
        allele2=a2,
        prob=prob,
        matching=matching,
        low_confidence=low_conf,
    )


class _StubRunner:
    """Stands in for HibagRunner; returns a canned result and records the call."""

    def __init__(self, result: HibagResult) -> None:
        self._result = result
        self.received: dict | None = None

    def predict_for_ancestry(self, plink_prefix, ancestry, out_dir, **kwargs) -> HibagResult:
        self.received = {"plink_prefix": plink_prefix, "ancestry": ancestry, "out_dir": out_dir}
        return self._result


def _read_calls(engine: sa.Engine) -> list[dict]:
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(sa.select(hla_calls)).fetchall()]


class TestPersistHlaCalls:
    def test_round_trip_and_low_confidence_mapping(self, sample_engine: sa.Engine) -> None:
        n = persist_hla_calls(
            sample_engine,
            [_call("A", "01:01", "02:01", 0.98, False), _call("B", "57:01", "07:02", 0.3, True)],
            ancestry_model="European",
        )
        assert n == 2
        rows = {r["locus"]: r for r in _read_calls(sample_engine)}
        assert rows["A"]["allele1"] == "01:01"
        assert rows["A"]["low_confidence"] == 0
        assert rows["A"]["ancestry_model"] == "European"
        assert rows["A"]["source"] == "hibag"
        assert rows["B"]["low_confidence"] == 1
        assert rows["B"]["prob"] == pytest.approx(0.3)

    def test_replace_is_idempotent(self, sample_engine: sa.Engine) -> None:
        persist_hla_calls(sample_engine, [_call("A", "01:01", "02:01", 0.9, False)])
        persist_hla_calls(sample_engine, [_call("B", "57:01", "07:02", 0.9, False)])
        rows = _read_calls(sample_engine)
        # Second call replaced the first (full-table replace), not accumulated.
        assert {r["locus"] for r in rows} == {"B"}

    def test_dedup_on_locus_last_write_wins(self, sample_engine: sa.Engine) -> None:
        persist_hla_calls(
            sample_engine,
            [_call("A", "01:01", "01:01", 0.9, False), _call("A", "02:01", "03:01", 0.8, False)],
        )
        rows = _read_calls(sample_engine)
        assert len(rows) == 1
        assert rows[0]["allele1"] == "02:01"


class TestPredictAndPersist:
    def test_unavailable_when_rscript_missing(
        self, sample_engine: sa.Engine, tmp_path: Path
    ) -> None:
        result = predict_and_persist_hla_calls(
            sample_engine,
            tmp_path / "work",
            rscript=tmp_path / "no-such-rscript",  # not a file -> detect_rscript None
            model_dir=tmp_path,
            ancestry="European",
        )
        assert result.status == STATUS_UNAVAILABLE
        assert result.n_persisted == 0
        assert _read_calls(sample_engine) == []

    def test_unavailable_when_model_missing(
        self, sample_engine: sa.Engine, tmp_path: Path
    ) -> None:
        # A resolvable Rscript but no {ancestry}-HLA4.RData in the model dir.
        fake_rscript = tmp_path / "Rscript"
        fake_rscript.write_text("#!/bin/sh\n")
        fake_rscript.chmod(0o755)
        result = predict_and_persist_hla_calls(
            sample_engine,
            tmp_path / "work",
            rscript=fake_rscript,
            model_dir=tmp_path / "empty_models",
            ancestry="European",
        )
        assert result.status == STATUS_UNAVAILABLE
        assert "model" in result.detail.lower()
        assert _read_calls(sample_engine) == []

    def test_no_input_when_no_hla_region_snp(
        self, sample_engine: sa.Engine, tmp_path: Path
    ) -> None:
        _insert_variants(sample_engine, [_row("rs1", "1", 100, "A", "G", "het")])  # off-region
        stub = _StubRunner(
            HibagResult(return_ok=True, calls=[_call("A", "01:01", "02:01", 0.9, False)])
        )
        result = predict_and_persist_hla_calls(
            sample_engine,
            tmp_path / "work",
            rscript=None,
            model_dir=None,
            ancestry="European",
            runner=stub,
        )
        assert result.status == STATUS_NO_INPUT
        assert stub.received is None  # never ran HIBAG
        assert _read_calls(sample_engine) == []

    def test_failed_run_leaves_table_untouched(
        self, sample_engine: sa.Engine, tmp_path: Path
    ) -> None:
        persist_hla_calls(
            sample_engine, [_call("C", "06:02", "07:01", 0.9, False)]
        )  # prior snapshot
        _insert_variants(sample_engine, [_row("rs_hla", "6", 31_431_780, "T", "G", "het")])
        stub = _StubRunner(HibagResult(return_ok=False, stderr_tail="boom"))
        result = predict_and_persist_hla_calls(
            sample_engine,
            tmp_path / "work",
            rscript=None,
            model_dir=None,
            ancestry="European",
            runner=stub,
        )
        assert result.status == STATUS_FAILED
        assert stub.received is not None  # HIBAG was invoked
        # Prior good snapshot preserved — a failed run never overwrites it.
        assert {r["locus"] for r in _read_calls(sample_engine)} == {"C"}

    def test_ok_persists_mocked_calls(self, sample_engine: sa.Engine, tmp_path: Path) -> None:
        _insert_variants(
            sample_engine,
            [
                _row("rs_hla1", "6", 31_431_780, "T", "G", "het"),
                _row("rs_hla2", "6", 31_274_203, "G", "A", "hom_ref"),
            ],
        )
        calls = [
            _call("A", "01:01", "02:01", 0.98, False),
            _call("B", "57:01", "07:02", 0.95, False),
        ]
        stub = _StubRunner(HibagResult(return_ok=True, calls=calls, runtime_seconds=1.5))
        result = predict_and_persist_hla_calls(
            sample_engine,
            tmp_path / "work",
            rscript=None,
            model_dir=None,
            ancestry="European",
            runner=stub,
        )
        assert result.status == STATUS_OK
        assert result.ok
        assert result.n_input_snps == 2
        assert result.n_calls == 2
        assert result.n_persisted == 2
        # The stub received the prepared PLINK prefix and the requested ancestry.
        assert stub.received["ancestry"] == "European"
        rows = {r["locus"]: r for r in _read_calls(sample_engine)}
        assert set(rows) == {"A", "B"}
        assert rows["B"]["ancestry_model"] == "European"

    def test_input_prep_io_error_is_failed_not_no_input(
        self, sample_engine: sa.Engine, tmp_path: Path, monkeypatch
    ) -> None:
        # A genuine I/O error preparing input must report `failed`, never crash and
        # never masquerade as the legitimate `no_input` empty result.
        def _boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr("backend.analysis.hla_persist.write_hibag_plink_input", _boom)
        stub = _StubRunner(
            HibagResult(return_ok=True, calls=[_call("A", "01:01", "02:01", 0.9, False)])
        )
        result = predict_and_persist_hla_calls(
            sample_engine,
            tmp_path / "work",
            rscript=None,
            model_dir=None,
            ancestry="European",
            runner=stub,
        )
        assert result.status == STATUS_FAILED
        assert stub.received is None  # never reached HIBAG
        assert _read_calls(sample_engine) == []

    def test_persist_db_error_leaves_table_untouched(
        self, sample_engine: sa.Engine, tmp_path: Path, monkeypatch
    ) -> None:
        persist_hla_calls(
            sample_engine, [_call("C", "06:02", "07:01", 0.9, False)]
        )  # prior snapshot
        _insert_variants(sample_engine, [_row("rs_hla", "6", 31_431_780, "T", "G", "het")])

        def _boom(*a, **k):
            raise sa.exc.SQLAlchemyError("db locked")

        monkeypatch.setattr("backend.analysis.hla_persist.persist_hla_calls", _boom)
        stub = _StubRunner(
            HibagResult(return_ok=True, calls=[_call("A", "01:01", "02:01", 0.9, False)])
        )
        result = predict_and_persist_hla_calls(
            sample_engine,
            tmp_path / "work",
            rscript=None,
            model_dir=None,
            ancestry="European",
            runner=stub,
        )
        assert result.status == STATUS_FAILED
        # Prior snapshot preserved (the failing persist was the monkeypatched one).
        assert {r["locus"] for r in _read_calls(sample_engine)} == {"C"}

    def test_runner_construction_filenotfound_is_unavailable(
        self, sample_engine: sa.Engine, tmp_path: Path, monkeypatch
    ) -> None:
        # detect_rscript + resolve_model pass, but building HibagRunner raises
        # FileNotFoundError (e.g. the bundled R script missing) -> graceful unavailable.
        fake_rscript = tmp_path / "Rscript"
        fake_rscript.write_text("#!/bin/sh\n")
        fake_rscript.chmod(0o755)
        (tmp_path / "European-HLA4.RData").write_bytes(b"model")
        _insert_variants(sample_engine, [_row("rs_hla", "6", 31_431_780, "T", "G", "het")])

        def _boom(*a, **k):
            raise FileNotFoundError("HIBAG R script not found")

        monkeypatch.setattr("backend.analysis.hla_persist.HibagRunner", _boom)
        result = predict_and_persist_hla_calls(
            sample_engine,
            tmp_path / "work",
            rscript=fake_rscript,
            model_dir=tmp_path,
            ancestry="European",
        )
        assert result.status == STATUS_UNAVAILABLE
        assert _read_calls(sample_engine) == []
