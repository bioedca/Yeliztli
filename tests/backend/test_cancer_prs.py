"""Tests for cancer PRS integration (P3-15).

Covers:
  - Weight set loading from JSON (4 cancer types)
  - PRS computation for breast, prostate, colorectal, melanoma
  - Unsupported PRS intervals are withheld
  - Ancestry mismatch propagation
  - Findings storage with module='cancer', category='prs'
  - Insufficient coverage handling
  - CancerPRSResult aggregation properties
  - API endpoints for cancer PRS
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from backend.analysis import cancer_prs as cancer_prs_module
from backend.analysis.allele_match import match_effect_allele_dosage
from backend.analysis.cancer_prs import (
    CANCER_PRS_TRAITS,
    CancerPRSResult,
    load_cancer_prs_weights,
    resolve_cancer_prs_sex_context,
    store_cancer_prs_findings,
)
from backend.analysis.cancer_prs import (
    run_cancer_prs as _run_cancer_prs,
)
from backend.analysis.prs import PRSResult, PRSWeightSet
from backend.db.tables import annotated_variants, findings

# ── Fixtures ──────────────────────────────────────────────────────────────

WEIGHTS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "backend"
    / "data"
    / "panels"
    / "cancer_prs_weights.json"
)
XX_CANCER_PRS_TRAITS = CANCER_PRS_TRAITS - {"prostate_cancer"}
XY_CANCER_PRS_TRAITS = CANCER_PRS_TRAITS - {"breast_cancer"}
UNRESOLVED_CANCER_PRS_TRAITS = CANCER_PRS_TRAITS - {
    "breast_cancer",
    "prostate_cancer",
}
CONFIRMED_RISK_ALLELE_FIXTURES = {
    ("breast_cancer", "rs2981582"): {
        "effect_allele": "A",
        "stored_other_allele": "G",
        "protective_allele": "G",
        "third_allele": None,
        "weight": 0.263,
    },
    ("breast_cancer", "rs11814448"): {
        "effect_allele": "C",
        "stored_other_allele": "A",
        "protective_allele": "A",
        "third_allele": None,
        "weight": 0.279,
    },
    ("breast_cancer", "rs6001930"): {
        "effect_allele": "C",
        "stored_other_allele": None,
        "protective_allele": "T",
        "third_allele": "G",
        "weight": 0.119,
    },
    ("melanoma", "rs12913832"): {
        "effect_allele": "G",
        "stored_other_allele": None,
        "protective_allele": "A",
        "third_allele": "C",
        "weight": 0.262,
    },
}


@pytest.fixture()
def cancer_weight_sets() -> list[PRSWeightSet]:
    """Load cancer PRS weight sets from the real JSON file."""
    return load_cancer_prs_weights(WEIGHTS_PATH)


def run_cancer_prs(*args, **kwargs) -> CancerPRSResult:
    """Test helper: keep legacy assertions in an explicit breast-eligible context."""
    kwargs.setdefault("inferred_sex", "XX")
    return _run_cancer_prs(*args, **kwargs)


@pytest.fixture()
def sample_with_prs_snps(sample_engine: sa.Engine) -> sa.Engine:
    """Sample engine with annotated variants matching cancer PRS SNPs.

    Includes SNPs from all four cancer PRS weight sets so coverage
    is sufficient for testing.
    """
    # Load real weight sets to get all rsids
    weight_sets = load_cancer_prs_weights(WEIGHTS_PATH)
    all_rsids: set[str] = set()
    for ws in weight_sets:
        all_rsids.update(ws.rsid_set())

    # Create variants for all PRS SNPs with deterministic genotypes
    variants = []
    for i, rsid in enumerate(sorted(all_rsids)):
        # Alternate genotypes: effect/effect, effect/ref, ref/ref
        alleles = ["A", "C", "G", "T"]
        a1 = alleles[i % 4]
        a2 = alleles[(i + 1) % 4]
        variants.append(
            {
                "rsid": rsid,
                "chrom": str((i % 22) + 1),
                "pos": 100000 + i * 1000,
                "genotype": f"{a1}{a2}",
                "annotation_coverage": 0,
            }
        )

    with sample_engine.begin() as conn:
        conn.execute(sa.insert(annotated_variants), variants)
    return sample_engine


@pytest.fixture()
def sample_partial_coverage(sample_engine: sa.Engine) -> sa.Engine:
    """Sample engine with only a few PRS SNPs — below 50% for most traits."""
    variants = [
        {
            "rsid": "rs2981582",
            "chrom": "10",
            "pos": 123456,
            "genotype": "GG",
            "annotation_coverage": 0,
        },
        {
            "rsid": "rs1447295",
            "chrom": "8",
            "pos": 128500000,
            "genotype": "AA",
            "annotation_coverage": 0,
        },
    ]
    with sample_engine.begin() as conn:
        conn.execute(sa.insert(annotated_variants), variants)
    return sample_engine


def _patch_cancer_run_dependencies(
    monkeypatch: pytest.MonkeyPatch, *, sex_context: str
) -> dict[str, object]:
    from backend.analysis import ancestry as ancestry_module
    from backend.analysis import cancer as cancer_module

    captured: dict[str, object] = {}

    monkeypatch.setattr(cancer_module, "load_cancer_panel", lambda: object())
    monkeypatch.setattr(
        cancer_module,
        "extract_cancer_variants",
        lambda _panel, _sample_engine: SimpleNamespace(
            panel_genes_checked=0,
            variants_in_panel_genes=0,
        ),
    )
    monkeypatch.setattr(
        cancer_module,
        "store_cancer_findings",
        lambda _result, _sample_engine, _reference_engine: 2,
    )
    monkeypatch.setattr(cancer_prs_module, "load_cancer_prs_weights", lambda: [])
    monkeypatch.setattr(ancestry_module, "get_inferred_ancestry", lambda _sample_engine: "EUR")
    monkeypatch.setattr(ancestry_module, "get_top_ancestry_fraction", lambda _sample_engine: 1.0)

    def fake_resolve(
        sample_engine: sa.Engine,
        *,
        reference_engine: object | None = None,
        sample_id: int | None = None,
    ) -> str:
        captured["resolved_sample_engine"] = sample_engine
        captured["reference_engine"] = reference_engine
        captured["sample_id"] = sample_id
        return sex_context

    def fake_run_cancer_prs(
        _weight_sets: list,
        _sample_engine: sa.Engine,
        **kwargs: object,
    ) -> SimpleNamespace:
        captured["inferred_sex"] = kwargs["inferred_sex"]
        captured["prs_reference_engine"] = kwargs.get("reference_engine")
        return SimpleNamespace(results=[])

    monkeypatch.setattr(cancer_prs_module, "resolve_cancer_prs_sex_context", fake_resolve)
    monkeypatch.setattr(cancer_prs_module, "run_cancer_prs", fake_run_cancer_prs)
    monkeypatch.setattr(cancer_prs_module, "store_cancer_prs_findings", lambda _result, _engine: 3)
    return captured


# ── Weight set loading tests ──────────────────────────────────────────────


class TestCancerPRSSexContextResolution:
    @pytest.mark.parametrize(
        ("recorded", "inferred", "expected"),
        [
            ("XX", None, "XX"),
            ("XY", "XX", "XY"),
            (None, "XY", "XY"),
            (None, "unknown", "unknown"),
        ],
    )
    def test_resolves_recorded_before_inferred(
        self,
        monkeypatch: pytest.MonkeyPatch,
        recorded: str | None,
        inferred: str | None,
        expected: str | None,
    ) -> None:
        from backend.services import sex_inference

        sample_engine = object()
        reference_engine = object()
        recorded_calls: list[tuple[object, int]] = []

        def fake_recorded(reference_arg: object, sample_id_arg: int) -> str | None:
            recorded_calls.append((reference_arg, sample_id_arg))
            return recorded

        monkeypatch.setattr(sex_inference, "infer_biological_sex", lambda _engine: inferred)
        monkeypatch.setattr(sex_inference, "get_recorded_biological_sex", fake_recorded)

        resolved = resolve_cancer_prs_sex_context(
            sample_engine,
            reference_engine=reference_engine,
            sample_id=42,
        )

        assert resolved == expected
        assert recorded_calls == [(reference_engine, 42)]

    def test_skips_recorded_lookup_without_sample_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backend.services import sex_inference

        monkeypatch.setattr(sex_inference, "infer_biological_sex", lambda _engine: "XY")
        monkeypatch.setattr(
            sex_inference,
            "get_recorded_biological_sex",
            lambda _reference_engine, _sample_id: pytest.fail("recorded lookup should be skipped"),
        )

        assert (
            resolve_cancer_prs_sex_context(
                object(),
                reference_engine=object(),
                sample_id=None,
            )
            == "XY"
        )


class TestCancerPRSCallSites:
    def test_api_run_uses_resolved_sex_for_prs(
        self, monkeypatch: pytest.MonkeyPatch, sample_engine: sa.Engine
    ) -> None:
        from backend.api.routes import cancer as cancer_routes

        reference_engine = object()
        captured = _patch_cancer_run_dependencies(monkeypatch, sex_context="XY")

        monkeypatch.setattr(cancer_routes, "_get_sample_engine", lambda _sample_id: sample_engine)
        monkeypatch.setattr(
            cancer_routes,
            "get_registry",
            lambda: SimpleNamespace(reference_engine=reference_engine),
        )

        response = cancer_routes.run_cancer_analysis(sample_id=42)

        assert response.findings_count == 2
        assert response.prs_findings_count == 3
        assert captured["resolved_sample_engine"] is sample_engine
        assert captured["reference_engine"] is reference_engine
        assert captured["sample_id"] == 42
        assert captured["inferred_sex"] == "XY"
        assert captured["prs_reference_engine"] is reference_engine

    def test_run_all_cancer_runner_uses_resolved_sex_for_prs(
        self, monkeypatch: pytest.MonkeyPatch, sample_engine: sa.Engine
    ) -> None:
        from backend.analysis import run_all

        reference_engine = object()
        captured = _patch_cancer_run_dependencies(monkeypatch, sex_context="XX")
        registry = SimpleNamespace(reference_engine=reference_engine)

        count = run_all._run_cancer(sample_engine, registry, sample_id=43)

        assert count == 5
        assert captured["resolved_sample_engine"] is sample_engine
        assert captured["reference_engine"] is reference_engine
        assert captured["sample_id"] == 43
        assert captured["inferred_sex"] == "XX"
        assert captured["prs_reference_engine"] is reference_engine

    def test_run_all_dispatch_passes_sample_id_to_cancer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backend.analysis import run_all

        captured: dict[str, int | None] = {}

        def fake_cancer_runner(
            _sample_engine: object,
            _registry: object,
            *,
            sample_id: int | None = None,
        ) -> int:
            captured["sample_id"] = sample_id
            return 7

        monkeypatch.setattr(run_all, "_get_modules", lambda: [("cancer", lambda *_args: 0)])
        monkeypatch.setattr(run_all, "_run_cancer", fake_cancer_runner)

        result = run_all.run_all_analyses(object(), object(), sample_id=44)

        assert result == {"cancer": 7}
        assert captured["sample_id"] == 44


class TestLoadCancerPRSWeights:
    """Test loading cancer PRS weight sets from JSON."""

    def test_loads_four_weight_sets(self, cancer_weight_sets: list[PRSWeightSet]) -> None:
        assert len(cancer_weight_sets) == 4

    def test_all_traits_present(self, cancer_weight_sets: list[PRSWeightSet]) -> None:
        traits = {ws.trait for ws in cancer_weight_sets}
        assert traits == CANCER_PRS_TRAITS

    def test_breast_cancer_weight_set(self, cancer_weight_sets: list[PRSWeightSet]) -> None:
        breast = [ws for ws in cancer_weight_sets if ws.trait == "breast_cancer"][0]
        assert breast.name == "Breast cancer (BCAC)"
        assert breast.source_ancestry == "EUR"
        assert breast.source_pmid == "30554720"
        assert breast.sample_size == 228951
        assert breast.snp_count > 0
        assert breast.module == "cancer"

    def test_prostate_cancer_weight_set(self, cancer_weight_sets: list[PRSWeightSet]) -> None:
        prostate = [ws for ws in cancer_weight_sets if ws.trait == "prostate_cancer"][0]
        assert prostate.name == "Prostate cancer (PRACTICAL)"
        assert prostate.source_pmid == "29892016"
        assert prostate.snp_count > 0

    def test_colorectal_cancer_weight_set(self, cancer_weight_sets: list[PRSWeightSet]) -> None:
        crc = [ws for ws in cancer_weight_sets if ws.trait == "colorectal_cancer"][0]
        assert crc.name == "Colorectal cancer (CRC)"
        assert crc.source_pmid == "30510241"
        assert crc.snp_count > 0

    def test_melanoma_weight_set(self, cancer_weight_sets: list[PRSWeightSet]) -> None:
        mel = [ws for ws in cancer_weight_sets if ws.trait == "melanoma"][0]
        assert mel.name == "Melanoma (GenoMEL)"
        assert mel.source_pmid == "32341527"
        assert mel.snp_count > 0

    def test_all_module_is_cancer(self, cancer_weight_sets: list[PRSWeightSet]) -> None:
        for ws in cancer_weight_sets:
            assert ws.module == "cancer"

    def test_weights_have_valid_structure(self, cancer_weight_sets: list[PRSWeightSet]) -> None:
        for ws in cancer_weight_sets:
            for w in ws.weights:
                assert w.rsid.startswith("rs")
                assert w.effect_allele in ("A", "C", "G", "T")
                assert isinstance(w.weight, float)

    def test_confirmed_inversion_fixes_are_risk_oriented(
        self, cancer_weight_sets: list[PRSWeightSet]
    ) -> None:
        by_trait = {ws.trait: {w.rsid: w for w in ws.weights} for ws in cancer_weight_sets}

        for (trait, rsid), expected in CONFIRMED_RISK_ALLELE_FIXTURES.items():
            snp_weight = by_trait[trait][rsid]

            assert snp_weight.effect_allele == expected["effect_allele"]
            assert snp_weight.other_allele == expected["stored_other_allele"]
            assert snp_weight.weight == pytest.approx(expected["weight"])

    def test_confirmed_protective_homozygotes_score_zero(
        self, cancer_weight_sets: list[PRSWeightSet]
    ) -> None:
        by_trait = {ws.trait: {w.rsid: w for w in ws.weights} for ws in cancer_weight_sets}

        for (trait, rsid), expected in CONFIRMED_RISK_ALLELE_FIXTURES.items():
            snp_weight = by_trait[trait][rsid]
            protective = match_effect_allele_dosage(
                expected["protective_allele"] * 2,
                snp_weight.effect_allele,
                snp_weight.other_allele,
                maf=None,
            )
            risk = match_effect_allele_dosage(
                expected["effect_allele"] * 2,
                snp_weight.effect_allele,
                snp_weight.other_allele,
                maf=None,
            )

            assert protective.dosage == 0
            assert risk.dosage == 2

    def test_multiallelic_third_alleles_do_not_score_as_strand_flips(
        self, cancer_weight_sets: list[PRSWeightSet]
    ) -> None:
        by_trait = {ws.trait: {w.rsid: w for w in ws.weights} for ws in cancer_weight_sets}

        for (trait, rsid), expected in CONFIRMED_RISK_ALLELE_FIXTURES.items():
            third_allele = expected["third_allele"]
            if third_allele is None:
                continue
            snp_weight = by_trait[trait][rsid]

            third = match_effect_allele_dosage(
                third_allele * 2,
                snp_weight.effect_allele,
                snp_weight.other_allele,
                maf=None,
            )

            assert third.dosage in (0, None)

    def test_bundled_sets_are_uncalibrated(self, cancer_weight_sets: list[PRSWeightSet]) -> None:
        """Shipped cancer weight sets carry only placeholder reference params, so
        they must load as uncalibrated and the engine withholds the percentile
        (issue #7)."""
        for ws in cancer_weight_sets:
            assert ws.calibrated is False

    def test_file_not_found_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_cancer_prs_weights(Path("/nonexistent/weights.json"))


# ── Cancer PRS computation tests ─────────────────────────────────────────


class TestRunCancerPRS:
    """Test running cancer PRS for sex-appropriate traits."""

    def test_computes_xx_eligible_traits(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        assert len(result.results) == len(XX_CANCER_PRS_TRAITS)
        traits = {r.trait for r in result.results}
        assert traits == XX_CANCER_PRS_TRAITS

    def test_prostate_prs_allowed_for_xy_context(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            inferred_sex="XY",
            n_bootstrap=100,
            rng_seed=42,
        )

        assert "prostate_cancer" in result.trait_names
        assert "breast_cancer" not in result.trait_names
        assert set(result.trait_names) == XY_CANCER_PRS_TRAITS

    def test_breast_prs_allowed_for_xx_context(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            inferred_sex="XX",
            n_bootstrap=100,
            rng_seed=42,
        )

        assert "breast_cancer" in result.trait_names
        assert "prostate_cancer" not in result.trait_names
        assert set(result.trait_names) == XX_CANCER_PRS_TRAITS

    @pytest.mark.parametrize(
        ("inferred_sex", "expected_traits"),
        [
            ("XX", XX_CANCER_PRS_TRAITS),
            ("unknown", UNRESOLVED_CANCER_PRS_TRAITS),
            ("manual_review", UNRESOLVED_CANCER_PRS_TRAITS),
            (None, UNRESOLVED_CANCER_PRS_TRAITS),
        ],
    )
    def test_prostate_prs_skipped_without_xy_context(
        self,
        cancer_weight_sets: list[PRSWeightSet],
        sample_with_prs_snps: sa.Engine,
        inferred_sex: str | None,
        expected_traits: frozenset[str],
    ) -> None:
        result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            inferred_sex=inferred_sex,
            n_bootstrap=100,
            rng_seed=42,
        )

        assert "prostate_cancer" not in result.trait_names
        assert set(result.trait_names) == expected_traits

    @pytest.mark.parametrize(
        ("inferred_sex", "expected_traits"),
        [
            ("XY", XY_CANCER_PRS_TRAITS),
            ("unknown", UNRESOLVED_CANCER_PRS_TRAITS),
            ("manual_review", UNRESOLVED_CANCER_PRS_TRAITS),
            (None, UNRESOLVED_CANCER_PRS_TRAITS),
        ],
    )
    def test_breast_prs_skipped_without_xx_context(
        self,
        cancer_weight_sets: list[PRSWeightSet],
        sample_with_prs_snps: sa.Engine,
        inferred_sex: str | None,
        expected_traits: frozenset[str],
    ) -> None:
        result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            inferred_sex=inferred_sex,
            n_bootstrap=100,
            rng_seed=42,
        )

        assert "breast_cancer" not in result.trait_names
        assert set(result.trait_names) == expected_traits

    def test_uncalibrated_sets_withhold_percentile(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        """The bundled sets are uncalibrated, so percentile / z-score / interval are
        withheld even when coverage is sufficient — no miscalibrated number is
        emitted (issue #7). raw_score is still computed."""
        result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        assert result.results
        for r in result.results:
            assert r.calibrated is False
            assert r.percentile is None
            assert r.z_score is None
            assert r.has_bootstrap_ci is False
            assert r.raw_score is not None

    def test_calibrated_set_still_emits_percentile(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        """A validated reference distribution produces a percentile, not an interval."""
        ws = replace(cancer_weight_sets[0], calibrated=True, reference_mean=0.5, reference_std=0.5)
        result = run_cancer_prs(
            [ws],
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        r = result.results[0]
        assert r.is_sufficient is True
        assert r.calibrated is True
        assert r.percentile is not None
        assert 0 <= r.percentile <= 100
        assert r.has_bootstrap_ci is False
        assert r.bootstrap_ci_lower is None
        assert r.bootstrap_ci_upper is None

    def test_all_evidence_level_is_1(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        """PRS components = ★☆☆☆ (evidence level 1)."""
        result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        for r in result.results:
            assert r.evidence_level == 1

    def test_ancestry_mismatch_propagated(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            inferred_ancestry="AFR",
            n_bootstrap=100,
            rng_seed=42,
        )
        for r in result.results:
            assert r.ancestry_mismatch is True
            assert r.ancestry_warning_text is not None
            assert "AFR" in r.ancestry_warning_text

    def test_no_mismatch_when_matching(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            inferred_ancestry="EUR",
            n_bootstrap=100,
            rng_seed=42,
        )
        for r in result.results:
            assert r.ancestry_mismatch is False

    def test_partial_coverage_mostly_insufficient(
        self, cancer_weight_sets: list[PRSWeightSet], sample_partial_coverage: sa.Engine
    ) -> None:
        """Only 2 SNPs present — eligible traits should be insufficient (<50%)."""
        result = run_cancer_prs(
            cancer_weight_sets,
            sample_partial_coverage,
            n_bootstrap=100,
            rng_seed=42,
        )
        # 2 SNPs out of 15-25 per trait is well below 50%.
        assert result.sufficient_count == 0
        assert len(result.insufficient_traits) == len(XX_CANCER_PRS_TRAITS)
        for r in result.results:
            assert r.is_sufficient is False

    def test_empty_sample_all_insufficient(
        self, cancer_weight_sets: list[PRSWeightSet], sample_engine: sa.Engine
    ) -> None:
        result = run_cancer_prs(
            cancer_weight_sets,
            sample_engine,
            n_bootstrap=100,
            rng_seed=42,
        )
        assert result.sufficient_count == 0
        assert len(result.insufficient_traits) == len(XX_CANCER_PRS_TRAITS)

    def test_reproducible_with_seed(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        r1 = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        r2 = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        for a, b in zip(r1.results, r2.results):
            assert a.percentile == b.percentile
            assert a.bootstrap_ci_lower == b.bootstrap_ci_lower
            assert a.bootstrap_ci_upper == b.bootstrap_ci_upper


# ── CancerPRSResult dataclass tests ──────────────────────────────────────


class TestCancerPRSResult:
    """Test CancerPRSResult aggregation properties."""

    def test_sufficient_count(self) -> None:
        result = CancerPRSResult(
            results=[
                PRSResult(
                    weight_set_name="A",
                    trait="a",
                    module="cancer",
                    source_ancestry="EUR",
                    source_study="Test",
                    source_pmid="1",
                    sample_size=1000,
                    raw_score=0.5,
                    coverage_fraction=0.8,
                ),
                PRSResult(
                    weight_set_name="B",
                    trait="b",
                    module="cancer",
                    source_ancestry="EUR",
                    source_study="Test",
                    source_pmid="2",
                    sample_size=1000,
                    raw_score=0.3,
                    coverage_fraction=0.3,
                ),
            ]
        )
        assert result.sufficient_count == 1

    def test_insufficient_traits(self) -> None:
        result = CancerPRSResult(
            results=[
                PRSResult(
                    weight_set_name="A",
                    trait="breast_cancer",
                    module="cancer",
                    source_ancestry="EUR",
                    source_study="Test",
                    source_pmid="1",
                    sample_size=1000,
                    raw_score=0.5,
                    coverage_fraction=0.3,
                ),
            ]
        )
        assert result.insufficient_traits == ["breast_cancer"]

    def test_trait_names(self) -> None:
        result = CancerPRSResult(
            results=[
                PRSResult(
                    weight_set_name="A",
                    trait="breast_cancer",
                    module="cancer",
                    source_ancestry="EUR",
                    source_study="Test",
                    source_pmid="1",
                    sample_size=1000,
                    raw_score=0.5,
                ),
                PRSResult(
                    weight_set_name="B",
                    trait="melanoma",
                    module="cancer",
                    source_ancestry="EUR",
                    source_study="Test",
                    source_pmid="2",
                    sample_size=1000,
                    raw_score=0.3,
                ),
            ]
        )
        assert result.trait_names == ["breast_cancer", "melanoma"]


# ── Findings storage tests ───────────────────────────────────────────────


class TestStoreCancerPRSFindings:
    """Test cancer PRS findings storage."""

    def test_stores_sufficient_results(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        prs_result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        count = store_cancer_prs_findings(prs_result, sample_with_prs_snps)
        assert count == prs_result.sufficient_count
        assert count > 0

    def test_findings_have_prs_category(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        prs_result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        store_cancer_prs_findings(prs_result, sample_with_prs_snps)

        with sample_with_prs_snps.connect() as conn:
            rows = conn.execute(
                sa.select(findings).where(
                    findings.c.module == "cancer",
                    findings.c.category == "prs",
                )
            ).fetchall()
        assert len(rows) > 0
        for row in rows:
            assert row.category == "prs"
            assert row.evidence_level == 1

    def test_finding_text_has_research_use_only(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        prs_result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        store_cancer_prs_findings(prs_result, sample_with_prs_snps)

        with sample_with_prs_snps.connect() as conn:
            rows = conn.execute(sa.select(findings).where(findings.c.category == "prs")).fetchall()
        for row in rows:
            assert "Research Use Only" in row.finding_text

    def test_uncalibrated_finding_text_and_percentile(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        """Stored uncalibrated findings report no percentile, both in the column
        and the human-readable text (issue #7)."""
        prs_result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        store_cancer_prs_findings(prs_result, sample_with_prs_snps)

        with sample_with_prs_snps.connect() as conn:
            rows = conn.execute(sa.select(findings).where(findings.c.category == "prs")).fetchall()
        assert rows  # sufficient-coverage findings are still stored
        for row in rows:
            assert row.prs_percentile is None
            assert "percentile" in row.finding_text.lower()
            assert "uncalibrated" in row.finding_text.lower()
            assert json.loads(row.detail_json)["calibrated"] is False

    def test_detail_json_has_trait(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        prs_result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        store_cancer_prs_findings(prs_result, sample_with_prs_snps)

        with sample_with_prs_snps.connect() as conn:
            rows = conn.execute(sa.select(findings).where(findings.c.category == "prs")).fetchall()
        for row in rows:
            detail = json.loads(row.detail_json)
            assert "trait" in detail
            assert detail["trait"] in CANCER_PRS_TRAITS

    def test_xx_rerun_clears_prostate_prs(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        xy_result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            inferred_sex="XY",
            n_bootstrap=100,
            rng_seed=42,
        )
        store_cancer_prs_findings(xy_result, sample_with_prs_snps)

        with sample_with_prs_snps.connect() as conn:
            initial_rows = conn.execute(
                sa.select(findings.c.detail_json).where(
                    findings.c.module == "cancer",
                    findings.c.category == "prs",
                )
            ).fetchall()

        initial_traits = {json.loads(row.detail_json)["trait"] for row in initial_rows}
        assert "prostate_cancer" in initial_traits
        assert "breast_cancer" not in initial_traits

        prs_result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            inferred_sex="XX",
            n_bootstrap=100,
            rng_seed=42,
        )
        count = store_cancer_prs_findings(prs_result, sample_with_prs_snps)

        with sample_with_prs_snps.connect() as conn:
            rows = conn.execute(
                sa.select(findings.c.detail_json).where(
                    findings.c.module == "cancer",
                    findings.c.category == "prs",
                )
            ).fetchall()

        stored_traits = {json.loads(row.detail_json)["trait"] for row in rows}
        expected_traits = {r.trait for r in prs_result.results if r.is_sufficient}
        assert expected_traits
        assert count == prs_result.sufficient_count
        assert "prostate_cancer" not in stored_traits
        assert stored_traits == expected_traits

    def test_xy_rerun_clears_breast_prs(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        xx_result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            inferred_sex="XX",
            n_bootstrap=100,
            rng_seed=42,
        )
        store_cancer_prs_findings(xx_result, sample_with_prs_snps)

        with sample_with_prs_snps.connect() as conn:
            initial_rows = conn.execute(
                sa.select(findings.c.detail_json).where(
                    findings.c.module == "cancer",
                    findings.c.category == "prs",
                )
            ).fetchall()

        initial_traits = {json.loads(row.detail_json)["trait"] for row in initial_rows}
        assert "breast_cancer" in initial_traits
        assert "prostate_cancer" not in initial_traits

        prs_result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            inferred_sex="XY",
            n_bootstrap=100,
            rng_seed=42,
        )
        count = store_cancer_prs_findings(prs_result, sample_with_prs_snps)

        with sample_with_prs_snps.connect() as conn:
            rows = conn.execute(
                sa.select(findings.c.detail_json).where(
                    findings.c.module == "cancer",
                    findings.c.category == "prs",
                )
            ).fetchall()

        stored_traits = {json.loads(row.detail_json)["trait"] for row in rows}
        expected_traits = {r.trait for r in prs_result.results if r.is_sufficient}
        assert expected_traits
        assert count == prs_result.sufficient_count
        assert "breast_cancer" not in stored_traits
        assert stored_traits == expected_traits

    def test_detail_json_withholds_unsupported_interval(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        prs_result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        store_cancer_prs_findings(prs_result, sample_with_prs_snps)

        with sample_with_prs_snps.connect() as conn:
            rows = conn.execute(sa.select(findings).where(findings.c.category == "prs")).fetchall()
        assert rows
        for row in rows:
            detail = json.loads(row.detail_json)
            assert "bootstrap_ci_lower" in detail
            assert "bootstrap_ci_upper" in detail
            assert detail["bootstrap_ci_lower"] is None
            assert detail["bootstrap_ci_upper"] is None
            assert detail["bootstrap_iterations"] == 0
            assert detail["research_use_only"] is True

    def test_does_not_store_insufficient(
        self, cancer_weight_sets: list[PRSWeightSet], sample_engine: sa.Engine
    ) -> None:
        """Results with < 50% coverage should not be stored."""
        prs_result = run_cancer_prs(
            cancer_weight_sets,
            sample_engine,
            n_bootstrap=100,
            rng_seed=42,
        )
        count = store_cancer_prs_findings(prs_result, sample_engine)
        assert count == 0

    def test_does_not_clear_monogenic_findings(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        """PRS storage should not affect monogenic findings."""
        with sample_with_prs_snps.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "cancer",
                        "category": "monogenic_variant",
                        "evidence_level": 4,
                        "finding_text": "BRCA1 rs80357906 — Pathogenic",
                    }
                ],
            )

        prs_result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        store_cancer_prs_findings(prs_result, sample_with_prs_snps)

        with sample_with_prs_snps.connect() as conn:
            monogenic = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(
                    findings.c.module == "cancer",
                    findings.c.category == "monogenic_variant",
                )
            ).scalar()
        assert monogenic == 1

    def test_empty_results_clear_stale_prs_finding(self, sample_engine: sa.Engine) -> None:
        """When the score DB is unavailable, run_cancer_prs yields empty results;
        store_cancer_prs_findings must then clear a stale cancer/prs finding rather
        than surface a previously computed percentile with broken provenance (#245)."""
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "cancer",
                        "category": "prs",
                        "evidence_level": 2,
                        "finding_text": "Stale breast cancer PRS: 90th percentile",
                    }
                ],
            )
        store_cancer_prs_findings(CancerPRSResult(results=[]), sample_engine)
        with sample_engine.connect() as conn:
            stale = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "cancer", findings.c.category == "prs")
            ).scalar()
        assert stale == 0

    def test_clears_previous_prs_on_rerun(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        prs_result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            n_bootstrap=100,
            rng_seed=42,
        )
        store_cancer_prs_findings(prs_result, sample_with_prs_snps)
        first_count = prs_result.sufficient_count

        # Run again
        store_cancer_prs_findings(prs_result, sample_with_prs_snps)

        with sample_with_prs_snps.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(
                    findings.c.module == "cancer",
                    findings.c.category == "prs",
                )
            ).scalar()
        assert count == first_count  # Not doubled
