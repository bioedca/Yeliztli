"""Tests for cancer PRS integration (P3-15).

Covers:
  - Weight-set loading from JSON (3 active models + 1 source-verified non-reporting model)
  - Disabled breast-cancer score containment and provenance auditability
  - PRS computation for active prostate, colorectal, and melanoma models
  - Unsupported PRS intervals are withheld
  - Ancestry mismatch propagation
  - Findings storage with module='cancer', category='prs'
  - Insufficient coverage handling
  - CancerPRSResult aggregation properties
  - API endpoints for cancer PRS
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from backend.analysis import cancer_prs as cancer_prs_module
from backend.analysis.allele_match import match_effect_allele_dosage
from backend.analysis.cancer_prs import (
    CancerPRSResult,
    load_cancer_prs_weights,
    resolve_cancer_prs_sex_context,
    store_cancer_prs_findings,
)
from backend.analysis.cancer_prs import (
    run_cancer_prs as _run_cancer_prs,
)
from backend.analysis.prs import PRSResult, PRSWeightSet, compute_prs, prs_model_fingerprint
from backend.db.tables import annotated_variants, findings

# ── Fixtures ──────────────────────────────────────────────────────────────

WEIGHTS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "backend"
    / "data"
    / "panels"
    / "cancer_prs_weights.json"
)
CANCER_PRS_TRAITS = frozenset(
    {
        "breast_cancer",
        "prostate_cancer",
        "colorectal_cancer",
        "melanoma",
    }
)
ACTIVE_CANCER_PRS_TRAITS = CANCER_PRS_TRAITS - {"breast_cancer"}
XX_CANCER_PRS_TRAITS = ACTIVE_CANCER_PRS_TRAITS - {"prostate_cancer"}
XY_CANCER_PRS_TRAITS = ACTIVE_CANCER_PRS_TRAITS
UNRESOLVED_CANCER_PRS_TRAITS = ACTIVE_CANCER_PRS_TRAITS - {"prostate_cancer"}
CONFIRMED_RISK_ALLELE_FIXTURES = {
    ("prostate_cancer", "rs12621278"): {
        "effect_allele": "A",
        "stored_other_allele": "G",
        "protective_allele": "G",
        "third_allele": None,
        "weight": 0.308,
    },
    ("colorectal_cancer", "rs4939827"): {
        "effect_allele": "T",
        "stored_other_allele": None,
        "protective_allele": "C",
        "third_allele": "A",
        "weight": 0.163,
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
    """Test helper: use a deterministic XX context unless a test overrides it."""
    kwargs.setdefault("inferred_sex", "XX")
    return _run_cancer_prs(*args, **kwargs)


@pytest.fixture()
def sample_with_prs_snps(sample_engine: sa.Engine) -> sa.Engine:
    """Sample engine with annotated variants matching cancer PRS SNPs.

    Includes SNPs from the three active models plus the non-reporting PRS77 rows
    so active-model coverage is sufficient and disabled rows remain loadable.
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
    monkeypatch.setattr(ancestry_module, "get_top_ancestry_fraction", lambda _sample_engine: 0.55)

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
        captured["inferred_ancestry"] = kwargs["inferred_ancestry"]
        captured["top_ancestry_fraction"] = kwargs["top_ancestry_fraction"]
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
        assert captured["inferred_ancestry"] == "EUR"
        assert captured["top_ancestry_fraction"] == 0.55
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
        assert captured["inferred_ancestry"] == "EUR"
        assert captured["top_ancestry_fraction"] == 0.55
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
        assert breast.name == "Breast cancer (Mavaddat PRS77; runtime blocked)"
        assert breast.source_ancestry == "EUR"
        assert breast.source_pmid == "25855707"
        assert breast.sample_size == 67054
        assert breast.snp_count == 77
        assert breast.module == "cancer"
        assert breast.pgs_id == "PGS000001"
        assert breast.pgs_license is None
        assert breast.genome_build == "GRCh37"
        assert breast.variants_number == 77
        assert breast.development_method
        assert breast.source_url == "https://doi.org/10.1093/jnci/djv036"
        assert all(weight.chrom and weight.pos for weight in breast.weights)
        assert breast.calibrated is False
        assert breast.scoring_enabled is False
        assert breast.calibration_eligible is False
        assert breast.runtime_scoring_blocked is True

    def test_legacy_breast_audit_hashes_recompute(self) -> None:
        payload = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
        breast = next(
            weight_set
            for weight_set in payload["weight_sets"]
            if weight_set["trait"] == "breast_cancer"
        )
        legacy = breast["legacy_audit_record"]

        def canonical_json(value: object) -> bytes:
            return (
                json.dumps(
                    value,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")

        legacy_object = dict(legacy["legacy_canonical_metadata"])
        legacy_object["weights"] = legacy["weights"]
        ordered_projection = [
            [
                weight["rsid"],
                weight["effect_allele"],
                weight.get("other_allele"),
                weight["weight"],
            ]
            for weight in legacy["weights"]
        ]
        representations = {
            "legacy_canonical_sha256": legacy_object,
            "legacy_weights_sha256": legacy["weights"],
            "legacy_ordered_projection_sha256": ordered_projection,
        }
        expected_hashes = {
            "legacy_canonical_sha256": (
                "5c1e91302d638fa30ea325d27e561179e0f66f30ae181c65f3b51a9965e912a0"
            ),
            "legacy_weights_sha256": (
                "8923dc246e4dd702040a891b9b8d9caf1b99c880f39141b3d7730e113ef3ad93"
            ),
            "legacy_ordered_projection_sha256": (
                "58eba9aff9a7bca7a25247d43332507f71d9d7474045312e7da0f82805f4f606"
            ),
        }

        for field, representation in representations.items():
            assert legacy[field] == expected_hashes[field]
            assert hashlib.sha256(canonical_json(representation)).hexdigest() == legacy[field]

    def test_loader_rejects_duplicate_traits(self, tmp_path: Path) -> None:
        payload = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
        payload["weight_sets"][1]["trait"] = payload["weight_sets"][0]["trait"]
        path = tmp_path / "duplicate-trait.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ValueError, match="duplicate top-level trait"):
            load_cancer_prs_weights(path)

    def test_loader_rejects_duplicate_rsids(self, tmp_path: Path) -> None:
        payload = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
        breast = payload["weight_sets"][0]
        breast["weights"][1]["rsid"] = breast["weights"][0]["rsid"]
        path = tmp_path / "duplicate-rsid.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ValueError, match="Duplicate rsID"):
            load_cancer_prs_weights(path)

    def test_loader_rejects_declared_variant_count_mismatch(self, tmp_path: Path) -> None:
        payload = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
        payload["weight_sets"][0]["variants_number"] = 76
        path = tmp_path / "variant-count.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ValueError, match="variants_number=76"):
            load_cancer_prs_weights(path)

    def test_loader_rejects_enabling_runtime_blocked_rows(self, tmp_path: Path) -> None:
        payload = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
        payload["weight_sets"][0]["scoring_enabled"] = True
        path = tmp_path / "unsafe-enabled-model.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ValueError, match="41 runtime-blocked row"):
            load_cancer_prs_weights(path)

    @pytest.mark.parametrize("field", ["calibrated", "calibration_eligible"])
    def test_loader_rejects_non_boolean_calibration_gates(
        self, tmp_path: Path, field: str
    ) -> None:
        payload = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
        payload["weight_sets"][0][field] = "false"
        path = tmp_path / f"non-boolean-{field}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ValueError, match=f"non-boolean {field}"):
            load_cancer_prs_weights(path)

    def test_loader_enforces_runtime_blocked_model_status(self, tmp_path: Path) -> None:
        payload = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
        breast = payload["weight_sets"][0]
        breast["scoring_enabled"] = True
        for weight in breast["weights"]:
            weight["runtime_scoring_eligible"] = True
        path = tmp_path / "blocked-model-status.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ValueError, match="enables scoring"):
            load_cancer_prs_weights(path)

    @pytest.mark.parametrize("tamper", ["missing", "null", "string"])
    def test_loader_rejects_invalid_runtime_row_markers(self, tmp_path: Path, tamper: str) -> None:
        payload = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
        breast = payload["weight_sets"][0]
        marker_row = breast["weights"][0]
        if tamper == "missing":
            marker_row.pop("runtime_scoring_eligible")
        elif tamper == "null":
            marker_row["runtime_scoring_eligible"] = None
        else:
            marker_row["runtime_scoring_eligible"] = "false"
        path = tmp_path / f"runtime-marker-{tamper}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(ValueError, match="missing or non-boolean"):
            load_cancer_prs_weights(path)

    def test_runtime_block_survives_scoring_flag_replacement(
        self, cancer_weight_sets: list[PRSWeightSet], sample_engine: sa.Engine
    ) -> None:
        breast = next(ws for ws in cancer_weight_sets if ws.trait == "breast_cancer")
        tampered = replace(breast, scoring_enabled=True)

        with pytest.raises(ValueError, match="runtime-blocked"):
            compute_prs(tampered, sample_engine)

    def test_weight_set_execution_flags_default_to_enabled(self) -> None:
        weight_set = PRSWeightSet(
            name="Test score",
            trait="test_trait",
            module="cancer",
            source_ancestry="EUR",
            source_study="Test study",
            source_pmid="1",
            sample_size=1,
            weights=[],
            reference_mean=0.0,
            reference_std=1.0,
        )

        assert weight_set.scoring_enabled is True
        assert weight_set.calibration_eligible is True

    def test_active_weight_sets_remain_enabled(
        self, cancer_weight_sets: list[PRSWeightSet]
    ) -> None:
        active_sets = {
            ws.trait: ws for ws in cancer_weight_sets if ws.trait in ACTIVE_CANCER_PRS_TRAITS
        }

        assert set(active_sets) == ACTIVE_CANCER_PRS_TRAITS
        for weight_set in active_sets.values():
            assert weight_set.scoring_enabled is True
            assert weight_set.calibration_eligible is True
            assert weight_set.runtime_scoring_blocked is False

    def test_model_fingerprints_are_deterministic_and_model_specific(
        self, cancer_weight_sets: list[PRSWeightSet]
    ) -> None:
        fingerprints = {
            weight_set.trait: prs_model_fingerprint(weight_set)
            for weight_set in cancer_weight_sets
        }

        assert len(set(fingerprints.values())) == len(cancer_weight_sets)
        assert all(len(fingerprint) == 64 for fingerprint in fingerprints.values())
        assert fingerprints == {
            weight_set.trait: prs_model_fingerprint(weight_set)
            for weight_set in cancer_weight_sets
        }

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

    def test_disabled_breast_prs_never_runs_for_xx_context(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        result = run_cancer_prs(
            cancer_weight_sets,
            sample_with_prs_snps,
            inferred_sex="XX",
            n_bootstrap=100,
            rng_seed=42,
        )

        assert "breast_cancer" not in result.trait_names
        assert "prostate_cancer" not in result.trait_names
        assert set(result.trait_names) == XX_CANCER_PRS_TRAITS

    def test_disabled_breast_model_never_reaches_run_prs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cancer_weight_sets: list[PRSWeightSet],
        sample_engine: sa.Engine,
    ) -> None:
        breast = next(ws for ws in cancer_weight_sets if ws.trait == "breast_cancer")
        colorectal = next(ws for ws in cancer_weight_sets if ws.trait == "colorectal_cancer")
        reference_engine = object()
        calls: list[tuple[PRSWeightSet, sa.Engine, dict[str, object]]] = []

        def fake_run_prs(
            weight_set: PRSWeightSet,
            engine: sa.Engine,
            **kwargs: object,
        ) -> PRSResult:
            calls.append((weight_set, engine, kwargs))
            return PRSResult(
                weight_set_name=weight_set.name,
                trait=weight_set.trait,
                module=weight_set.module,
                source_ancestry=weight_set.source_ancestry,
                source_study=weight_set.source_study,
                source_pmid=weight_set.source_pmid,
                sample_size=weight_set.sample_size,
                raw_score=0.0,
                coverage_fraction=1.0,
            )

        monkeypatch.setattr(cancer_prs_module, "run_prs", fake_run_prs)

        result = _run_cancer_prs(
            [breast, colorectal],
            sample_engine,
            inferred_ancestry="AFR",
            top_ancestry_fraction=0.75,
            inferred_sex="XX",
            n_bootstrap=17,
            rng_seed=9,
            reference_engine=reference_engine,
        )

        assert result.trait_names == ["colorectal_cancer"]
        assert len(calls) == 1
        called_weight_set, called_engine, called_kwargs = calls[0]
        assert called_weight_set is colorectal
        assert called_engine is sample_engine
        assert called_kwargs["inferred_ancestry"] == "AFR"
        assert called_kwargs["top_ancestry_fraction"] == 0.75
        assert called_kwargs["n_bootstrap"] == 17
        assert called_kwargs["rng_seed"] == 9
        assert called_kwargs["reference_engine"] is reference_engine

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
        active_weight_set = next(
            ws for ws in cancer_weight_sets if ws.trait == "colorectal_cancer"
        )
        ws = replace(active_weight_set, calibrated=True, reference_mean=0.5, reference_std=0.5)
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
        expected_fingerprints = {
            weight_set.trait: prs_model_fingerprint(weight_set)
            for weight_set in cancer_weight_sets
            if weight_set.scoring_enabled
        }
        assert all(
            result.model_fingerprint == expected_fingerprints[result.trait]
            for result in prs_result.results
        )

    def test_rejects_disabled_or_wrong_model_fingerprint(self, sample_engine: sa.Engine) -> None:
        weight_sets = load_cancer_prs_weights(WEIGHTS_PATH)
        breast_weight_set = next(
            weight_set for weight_set in weight_sets if weight_set.trait == "breast_cancer"
        )
        breast_result = PRSResult(
            weight_set_name="Synthetic non-reporting breast score",
            trait="breast_cancer",
            module="cancer",
            source_ancestry="EUR",
            source_study="Unverified",
            source_pmid="30554720",
            sample_size=228951,
            raw_score=1.2,
            z_score=1.0,
            percentile=84.0,
            calibrated=True,
            snps_used=25,
            snps_total=25,
            coverage_fraction=1.0,
            model_fingerprint=prs_model_fingerprint(breast_weight_set),
        )
        colorectal_result = replace(
            breast_result,
            weight_set_name="Synthetic active colorectal score",
            trait="colorectal_cancer",
            source_study="Active test model",
            source_pmid="30510241",
            model_fingerprint="0" * 64,
        )

        count = store_cancer_prs_findings(
            CancerPRSResult(results=[breast_result, colorectal_result]),
            sample_engine,
        )

        with sample_engine.connect() as conn:
            rows = conn.execute(
                sa.select(findings.c.detail_json).where(
                    findings.c.module == "cancer",
                    findings.c.category == "prs",
                )
            ).fetchall()

        assert count == 0
        assert rows == []

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
            assert row.prs_score is None
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
        expected_fingerprints = {
            weight_set.trait: prs_model_fingerprint(weight_set)
            for weight_set in cancer_weight_sets
            if weight_set.scoring_enabled
        }
        for row in rows:
            detail = json.loads(row.detail_json)
            assert "trait" in detail
            assert detail["trait"] in CANCER_PRS_TRAITS
            assert detail["model_fingerprint"] == expected_fingerprints[detail["trait"]]

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

    def test_active_rerun_clears_stale_breast_prs(
        self, cancer_weight_sets: list[PRSWeightSet], sample_with_prs_snps: sa.Engine
    ) -> None:
        with sample_with_prs_snps.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "cancer",
                        "category": "prs",
                        "evidence_level": 1,
                        "finding_text": "Stale breast cancer PRS",
                        "detail_json": json.dumps({"trait": "breast_cancer"}),
                    }
                ],
            )

        with sample_with_prs_snps.connect() as conn:
            initial_rows = conn.execute(
                sa.select(findings.c.detail_json).where(
                    findings.c.module == "cancer",
                    findings.c.category == "prs",
                )
            ).fetchall()

        initial_traits = {json.loads(row.detail_json)["trait"] for row in initial_rows}
        assert initial_traits == {"breast_cancer"}

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
