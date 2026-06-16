"""Tests for the hereditary haemochromatosis (HFE) module.

Seeds synthetic genotypes into a real sample DB and asserts the produced
findings: genotype-combination classification, sex-stratified penetrance, the
carriage/negative gate, and indeterminate handling for off-chip / no-call probes.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from backend.analysis.hemochromatosis import (
    assess_hemochromatosis,
    load_hemochromatosis_panel,
    store_hemochromatosis_findings,
)
from backend.db.tables import findings, individuals, raw_variants, reference_metadata, samples


@pytest.fixture()
def panel():
    return load_hemochromatosis_panel()


def _seed(engine: sa.Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(raw_variants), rows)


def _xy_chr_rows() -> list[dict]:
    """chrX non-PAR homozygous + chrY typed at evaluable densities (≥100 non-PAR
    chrX, ≥50 chrY — issue #363) → infer_biological_sex == 'XY'."""
    rows = [
        {"rsid": f"rsX{i}", "chrom": "X", "pos": 50_000_000 + i, "genotype": "GG"}
        for i in range(120)
    ]
    rows += [
        {"rsid": f"rsY{i}", "chrom": "Y", "pos": 2_700_000 + i, "genotype": "GG"}
        for i in range(60)
    ]
    return rows


class TestC282YHomozygous:
    def test_homozygous_finding_sex_indeterminate(self, panel, sample_engine: sa.Engine) -> None:
        # Only the HFE SNP seeded → no chrX/chrY → sex indeterminate → both
        # penetrance figures shown.
        _seed(
            sample_engine, [{"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "genotype": "AA"}]
        )
        a = assess_hemochromatosis(panel, sample_engine)

        assert len(a.calls) == 1
        call = a.calls[0]
        assert call.risk_classification == "C282Y homozygous"
        assert call.zygosity == "hom_alt"
        assert call.evidence_stars == 3
        assert "56.4%" in call.finding_text
        assert "40.5%" in call.finding_text

    def test_homozygous_sex_stratified_male(self, panel, sample_engine: sa.Engine) -> None:
        _seed(
            sample_engine,
            [
                {"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "genotype": "AA"},
                *_xy_chr_rows(),
            ],
        )
        a = assess_hemochromatosis(panel, sample_engine)
        assert a.sex_used == "XY"
        assert a.calls[0].detail["sex_used"] == "XY"
        assert "56.4%" in a.calls[0].finding_text  # male figure emphasised


class TestNegativeAndCombinations:
    def test_homozygous_reference_no_finding(self, panel, sample_engine: sa.Engine) -> None:
        _seed(
            sample_engine,
            [
                {"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "genotype": "GG"},
                {"rsid": "rs1799945", "chrom": "6", "pos": 26091179, "genotype": "CC"},
            ],
        )
        a = assess_hemochromatosis(panel, sample_engine)
        assert a.calls == []  # carriage/negative gate
        # C282Y (rs1800562, A/G) is confidently reference, but H63D (rs1799945) is the
        # palindromic C/G SNP — its "CC" homozygote is strand-ambiguous (minus-strand
        # "GG"), so it is reported indeterminate rather than confidently reference
        # (#844). No finding either way.
        assert a.indeterminate_loci == ["rs1799945"]

    def test_compound_heterozygous(self, panel, sample_engine: sa.Engine) -> None:
        _seed(
            sample_engine,
            [
                {"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "genotype": "AG"},
                {"rsid": "rs1799945", "chrom": "6", "pos": 26091179, "genotype": "CG"},
            ],
        )
        a = assess_hemochromatosis(panel, sample_engine)
        assert len(a.calls) == 1
        assert (
            a.calls[0].risk_classification == "Compound heterozygous (C282Y/H63D), phase-inferred"
        )
        assert a.calls[0].evidence_stars == 2
        assert (
            "low-penetrance" in a.calls[0].finding_text.lower()
            or "low penetrance" in a.calls[0].finding_text.lower()
        )

    def test_compound_heterozygous_is_phase_inferred(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        """Unphased C282Y/H63D het+het cannot prove trans, so the call is phase-inferred.

        SNP-array genotypes are unphased; the same observed genotype arises whether
        C282Y and H63D are in trans (true compound het) or the rare cis configuration
        (documented by family genotyping, Best et al. 2001, PMID 11531973). The call
        must carry a phase-inference caveat rather than a definitive label.
        Regression for issue #101.
        """
        _seed(
            sample_engine,
            [
                {"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "genotype": "AG"},
                {"rsid": "rs1799945", "chrom": "6", "pos": 26091179, "genotype": "CG"},
            ],
        )
        a = assess_hemochromatosis(panel, sample_engine)
        assert len(a.calls) == 1
        call = a.calls[0]
        # The classification label alone is qualified — a consumer reading only
        # risk_classification must not see an unqualified compound-het call.
        assert "phase-inferred" in call.risk_classification.lower()
        # Phase caveat travels with the user-facing finding text…
        assert "phase-inferred" in call.finding_text.lower()
        assert "trans" in call.finding_text.lower()
        # …and is present as a structured caveat in the detail JSON.
        caveats_text = " ".join(call.detail["caveats"]).lower()
        assert "unphased" in caveats_text
        assert "cis" in caveats_text

    def test_c282y_single_heterozygote(self, panel, sample_engine: sa.Engine) -> None:
        _seed(
            sample_engine,
            [
                {"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "genotype": "AG"},
                {"rsid": "rs1799945", "chrom": "6", "pos": 26091179, "genotype": "CC"},
            ],
        )
        a = assess_hemochromatosis(panel, sample_engine)
        assert len(a.calls) == 1
        assert a.calls[0].risk_classification == "C282Y heterozygous (carrier)"
        assert a.calls[0].evidence_stars == 2


class TestIndeterminate:
    def test_off_chip_c282y_is_indeterminate(self, panel, sample_engine: sa.Engine) -> None:
        # rs1800562 absent (off-chip); H63D hom-ref → no positive finding, and
        # C282Y must be flagged indeterminate, not a false "clear".
        _seed(
            sample_engine, [{"rsid": "rs1799945", "chrom": "6", "pos": 26091179, "genotype": "CC"}]
        )
        a = assess_hemochromatosis(panel, sample_engine)
        assert a.calls == []
        assert "rs1800562" in a.indeterminate_loci

    def test_no_call_c282y_is_indeterminate(self, panel, sample_engine: sa.Engine) -> None:
        _seed(
            sample_engine, [{"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "genotype": "--"}]
        )
        a = assess_hemochromatosis(panel, sample_engine)
        assert a.calls == []
        assert "rs1800562" in a.indeterminate_loci


class TestStorage:
    def test_findings_stored_with_module_and_category(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        _seed(
            sample_engine, [{"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "genotype": "AA"}]
        )
        a = assess_hemochromatosis(panel, sample_engine)
        count = store_hemochromatosis_findings(a, sample_engine)
        assert count == 1

        with sample_engine.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.module == "hemochromatosis")
            ).fetchone()
        assert row.module == "hemochromatosis"
        assert row.category == "risk_genotype"
        assert row.gene_symbol == "HFE"
        assert row.clinvar_significance is None
        detail = json.loads(row.detail_json)
        assert detail["genotype_calls"]["rs1800562"] == "AA"
        assert "rs1799945" in detail["indeterminate_loci"]  # H63D not seeded

    def test_rerun_idempotent(self, panel, sample_engine: sa.Engine) -> None:
        _seed(
            sample_engine, [{"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "genotype": "AA"}]
        )
        a = assess_hemochromatosis(panel, sample_engine)
        store_hemochromatosis_findings(a, sample_engine)
        store_hemochromatosis_findings(a, sample_engine)
        with sample_engine.connect() as conn:
            count = conn.execute(
                sa.select(sa.func.count())
                .select_from(findings)
                .where(findings.c.module == "hemochromatosis")
            ).scalar()
        assert count == 1


class TestCitationProvenance:
    """Guard the curated HFE evidence links (issue #175).

    PMID 21149639 (a GPER1/cytokeratin trafficking paper) was wrongly cited by
    the H63D-containing models. This locks the panel's PMIDs to a reviewed
    allowlist of genuine HFE/hemochromatosis references so an off-topic PMID
    cannot silently reappear, and pins the per-model citations the fix landed.
    """

    # Every PMID below was verified (PubMed + Consensus) to be an HFE /
    # hereditary-hemochromatosis reference. Add here only after confirming a new
    # PMID actually concerns HFE, never as a convenience.
    _HFE_PMID_ALLOWLIST = frozenset(
        {
            "38479735",  # BMJ Open 2024 — HFE C282Y cohort
            "30651232",  # BMJ 2019 — HFE genotype penetrance cohort
            "11531973",  # Best 2001 — C282Y/H63D cis vs trans phase
            "36196271",  # Hasan 2022 — C282Y/H63D low-penetrance genotype
            "19554541",  # Gurrin 2009 (HealthIron) — C282Y/H63D low morbidity
            "24729993",  # Kelley 2014 — iron overload rare in H63D homozygotes
            "11399207",  # Burke 2000 — pooled HFE genotype/iron-overload analysis
        }
    )

    # The unrelated paper that must never be cited by this panel again.
    _BANNED_PMID = "21149639"

    def test_no_unrelated_gper1_pmid(self, panel) -> None:
        for model in panel.genotype_models:
            assert self._BANNED_PMID not in model.pmids, (
                f"model {model.id!r} cites unrelated PMID {self._BANNED_PMID}"
            )

    def test_all_pmids_are_curated_hfe_references(self, panel) -> None:
        for model in panel.genotype_models:
            unknown = set(model.pmids) - self._HFE_PMID_ALLOWLIST
            assert not unknown, (
                f"model {model.id!r} cites non-allowlisted PMID(s) {sorted(unknown)}; "
                f"verify they are genuine HFE references before adding to the allowlist"
            )

    def test_h63d_models_have_h63d_evidence(self, panel) -> None:
        # The three H63D-containing models must carry at least one verified
        # HFE/H63D reference (not be left citation-less after the fix).
        by_id = {m.id: m for m in panel.genotype_models}
        for model_id in ("compound_heterozygous", "h63d_homozygous", "h63d_heterozygous"):
            assert by_id[model_id].pmids, f"{model_id} lost its evidence citations"


class TestRecordedSexPrecedence:
    """#399 — a user-recorded ``individuals.biological_sex`` is authoritative over
    array inference for the sex-stratified C282Y-homozygote penetrance (male 56.4%
    vs female 40.5% cumulative diagnosis by age 80; Lucas 2024, PMID 38479735),
    mirroring resolve_biological_sex / the breast absolute-risk overlay.
    """

    def _ref_engine_with_recorded_sex(self, biological_sex: str) -> sa.Engine:
        eng = sa.create_engine("sqlite://")
        reference_metadata.create_all(eng)
        with eng.begin() as conn:
            conn.execute(
                sa.insert(individuals).values(
                    id=1, display_name="P", biological_sex=biological_sex
                )
            )
            conn.execute(
                sa.insert(samples).values(id=1, name="s", db_path="samples/s.db", individual_id=1)
            )
        return eng

    def test_recorded_xx_resolves_unavailable_inference(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        # Only the HFE SNP seeded → no chrX/chrY → inference is indeterminate; a
        # recorded XX must resolve the female-specific penetrance (not show both).
        _seed(
            sample_engine, [{"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "genotype": "AA"}]
        )
        ref = self._ref_engine_with_recorded_sex("XX")
        a = assess_hemochromatosis(panel, sample_engine, reference_engine=ref, sample_id=1)

        assert a.sex_used == "XX"
        call = a.calls[0]
        assert call.detail["sex_used"] == "XX"
        assert call.detail["sex_source"] == "recorded"
        assert call.detail["sex_conflict"] is False
        assert "In women" in call.finding_text and "40.5%" in call.finding_text
        assert "In men" not in call.finding_text and "56.4%" not in call.finding_text

    def test_recorded_xx_overrides_discordant_inferred_xy(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        # The array infers XY, but a recorded XX wins and flags the discordance.
        _seed(
            sample_engine,
            [
                {"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "genotype": "AA"},
                *_xy_chr_rows(),
            ],
        )
        ref = self._ref_engine_with_recorded_sex("XX")
        a = assess_hemochromatosis(panel, sample_engine, reference_engine=ref, sample_id=1)

        assert a.sex_used == "XX"  # recorded beats a confident inference
        call = a.calls[0]
        assert call.detail["sex_source"] == "recorded"
        assert call.detail["sex_conflict"] is True
        assert "In women" in call.finding_text

    def test_falls_back_to_inference_without_recorded(
        self, panel, sample_engine: sa.Engine
    ) -> None:
        # No reference_engine/sample_id → prior behaviour: inferred sex drives it.
        _seed(
            sample_engine,
            [
                {"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "genotype": "AA"},
                *_xy_chr_rows(),
            ],
        )
        a = assess_hemochromatosis(panel, sample_engine)

        assert a.sex_used == "XY"
        assert a.calls[0].detail["sex_source"] == "inferred"
        assert a.calls[0].detail["sex_conflict"] is False
