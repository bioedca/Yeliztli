"""Tests for SW-B4: PGS Catalog → PRS engine bridge + ancestry-aware selection.

Covers:
  - Positional (chrom:pos) matching in compute_prs for rsID-less scores.
  - Registry loading + the select_pgs_for_ancestry policy (prefer multi-ancestry,
    then ancestry match, then any multi, then first).
  - build_weight_set_from_pgs: provenance population, positional weights,
    graceful absence when the score is not in pgs_scores.db.
"""

from __future__ import annotations

import sqlalchemy as sa

from backend.analysis.pgs_bridge import (
    PgsScoreSpec,
    _covers,
    _resolve_source_ancestry,
    build_trait_weight_set,
    build_weight_set_from_pgs,
    load_pgs_registry,
    select_pgs_for_ancestry,
)
from backend.analysis.prs import (
    PRSResult,
    PRSSNPWeight,
    PRSWeightSet,
    check_ancestry_mismatch,
    compute_prs,
)
from backend.annotation.pgs_catalog import (
    create_pgs_tables,
    pgs_score_metadata,
    pgs_score_weights,
)
from backend.db.tables import annotated_variants

# ── Fixtures ──────────────────────────────────────────────────────────────


def _pgs_engine() -> sa.Engine:
    """In-memory pgs_scores.db with one rsID score and one positional score."""
    engine = sa.create_engine("sqlite://")
    create_pgs_tables(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(pgs_score_metadata),
            [
                {
                    "pgs_id": "PGS000713",
                    "pgs_name": "T2D",
                    "trait_reported": "Type 2 diabetes",
                    "trait_efo": "MONDO_0005148",
                    "genome_build": "GRCh37",
                    "variants_number": 3,
                    "weight_type": "beta",
                    "license": "CC-BY-4.0",
                    "license_bundle_ok": 1,
                    "citation": "Sinnott-Armstrong 2021",
                    "pgp_id": None,
                },
                {
                    "pgs_id": "PGS005198",
                    "pgs_name": "BMI",
                    "trait_reported": "Body mass index",
                    "trait_efo": "EFO_0004340",
                    "genome_build": "GRCh37",
                    "variants_number": 2,
                    "weight_type": "beta",
                    "license": "CC-BY-4.0",
                    "license_bundle_ok": 1,
                    "citation": "Smit 2025",
                    "pgp_id": None,
                },
            ],
        )
        conn.execute(
            sa.insert(pgs_score_weights),
            [
                # rsID-bearing score
                {
                    "pgs_id": "PGS000713",
                    "rsid": "rs1",
                    "chrom": "1",
                    "pos": 100,
                    "effect_allele": "A",
                    "other_allele": "G",
                    "effect_weight": 0.5,
                },
                {
                    "pgs_id": "PGS000713",
                    "rsid": "rs2",
                    "chrom": "2",
                    "pos": 200,
                    "effect_allele": "T",
                    "other_allele": "C",
                    "effect_weight": 0.3,
                },
                # positional-only score (no rsID, mirrors PGS005198 harmonized files)
                {
                    "pgs_id": "PGS005198",
                    "rsid": None,
                    "chrom": "1",
                    "pos": 100,
                    "effect_allele": "A",
                    "other_allele": "G",
                    "effect_weight": 0.2,
                },
                {
                    "pgs_id": "PGS005198",
                    "rsid": None,
                    "chrom": "3",
                    "pos": 300,
                    "effect_allele": "T",
                    "other_allele": "C",
                    "effect_weight": 0.4,
                },
            ],
        )
    return engine


def _spec(
    pgs_id: str, *, multi: bool, ancestries: list[str], bundle_ok: bool = True
) -> PgsScoreSpec:
    return PgsScoreSpec(
        pgs_id=pgs_id,
        module="metabolic",
        name=pgs_id,
        trait_label="t",
        method="PRS-CS" if multi else "snpnet",
        multi_ancestry=multi,
        ancestries=ancestries,
        source_study="study",
        source_pmid="1",
        sample_size=1000,
        license="CC-BY-4.0",
        source_url="https://example/" + pgs_id,
        bundle_ok=bundle_ok,
    )


# ── Positional matching in the engine ──────────────────────────────────────


class TestPositionalMatching:
    def test_positional_dosage(self, sample_engine: sa.Engine) -> None:
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": "rsX",
                        "chrom": "1",
                        "pos": 100,
                        "genotype": "AA",
                        "gnomad_af_global": 0.2,
                        "annotation_coverage": 0,
                    },
                    {
                        "rsid": "rsY",
                        "chrom": "3",
                        "pos": 300,
                        "genotype": "TC",
                        "gnomad_af_global": 0.3,
                        "annotation_coverage": 0,
                    },
                ],
            )
        ws = PRSWeightSet(
            name="pos",
            trait="bmi",
            module="metabolic",
            source_ancestry="EUR",
            source_study="s",
            source_pmid="1",
            sample_size=1,
            weights=[
                PRSSNPWeight(
                    rsid="", effect_allele="A", weight=0.2, other_allele="G", chrom="1", pos=100
                ),
                PRSSNPWeight(
                    rsid="", effect_allele="T", weight=0.4, other_allele="C", chrom="3", pos=300
                ),
            ],
            reference_mean=0.0,
            reference_std=1.0,
        )
        result = compute_prs(ws, sample_engine)
        # rsX AA -> 2 copies of A (0.2*2=0.4); rsY TC -> 1 copy of T (0.4*1=0.4)
        assert result.snps_used == 2
        assert abs(result.raw_score - 0.8) < 1e-9

    def test_positional_chrom_prefix_normalized(self, sample_engine: sa.Engine) -> None:
        # Sample stores "chr1"; weight stores "1" — must still match.
        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(annotated_variants),
                [
                    {
                        "rsid": "rsX",
                        "chrom": "chr1",
                        "pos": 100,
                        "genotype": "AA",
                        "gnomad_af_global": 0.2,
                        "annotation_coverage": 0,
                    }
                ],
            )
        ws = PRSWeightSet(
            name="pos",
            trait="bmi",
            module="metabolic",
            source_ancestry="EUR",
            source_study="s",
            source_pmid="1",
            sample_size=1,
            weights=[
                PRSSNPWeight(
                    rsid="", effect_allele="A", weight=0.2, other_allele="G", chrom="1", pos=100
                ),
            ],
            reference_mean=0.0,
            reference_std=1.0,
        )
        result = compute_prs(ws, sample_engine)
        assert result.snps_used == 1


# ── Registry + selection policy ────────────────────────────────────────────


class TestSelection:
    def test_prefers_multi_ancestry_covering(self) -> None:
        specs = [
            _spec("EUR_ONLY", multi=False, ancestries=["EUR"]),
            _spec("MULTI", multi=True, ancestries=["AFR", "AMR", "EAS", "EUR", "SAS"]),
        ]
        assert select_pgs_for_ancestry(specs, "AFR").pgs_id == "MULTI"
        # Even when a single-ancestry score matches, multi-ancestry wins.
        assert select_pgs_for_ancestry(specs, "EUR").pgs_id == "MULTI"

    def test_single_ancestry_match_when_no_multi(self) -> None:
        specs = [
            _spec("EUR_ONLY", multi=False, ancestries=["EUR"]),
            _spec("EAS_ONLY", multi=False, ancestries=["EAS"]),
        ]
        assert select_pgs_for_ancestry(specs, "EAS").pgs_id == "EAS_ONLY"

    def test_any_multi_when_ancestry_uncovered(self) -> None:
        specs = [
            _spec("EUR_ONLY", multi=False, ancestries=["EUR"]),
            _spec("MULTI", multi=True, ancestries=["EUR", "EAS"]),
        ]
        # MID is covered by neither; the multi-ancestry score still transfers best.
        assert select_pgs_for_ancestry(specs, "MID").pgs_id == "MULTI"

    def test_falls_back_to_first(self) -> None:
        specs = [_spec("EUR_ONLY", multi=False, ancestries=["EUR"])]
        assert select_pgs_for_ancestry(specs, "AFR").pgs_id == "EUR_ONLY"

    def test_bundle_only_filters_user_fetch(self) -> None:
        specs = [_spec("NC", multi=True, ancestries=["EUR"], bundle_ok=False)]
        assert select_pgs_for_ancestry(specs, "EUR") is None
        assert select_pgs_for_ancestry(specs, "EUR", bundle_only=False).pgs_id == "NC"

    def test_registry_loads_shipped_scores(self) -> None:
        reg = load_pgs_registry()
        assert {"type_2_diabetes", "body_mass_index", "ldl_cholesterol"} <= set(reg)
        bmi = reg["body_mass_index"][0]
        assert bmi.pgs_id == "PGS005198"
        assert bmi.multi_ancestry is True
        assert bmi.license == "CC-BY-4.0" and bmi.bundle_ok is True


# ── Weight-set construction ────────────────────────────────────────────────


class TestBuildWeightSet:
    def test_builds_rsid_score_with_provenance(self) -> None:
        spec = _spec("PGS000713", multi=False, ancestries=["EUR"])
        ws = build_weight_set_from_pgs(
            _pgs_engine(), spec, "type_2_diabetes", inferred_ancestry="EUR"
        )
        assert ws is not None
        assert ws.pgs_id == "PGS000713"
        assert ws.pgs_license == "CC-BY-4.0"
        assert ws.development_method == "snpnet"
        assert ws.genome_build == "GRCh37"
        assert ws.calibrated is False
        assert ws.snp_count == 2
        assert {w.rsid for w in ws.weights} == {"rs1", "rs2"}
        # EUR-only score, inferred EUR -> no mismatch (source resolved to EUR).
        assert ws.source_ancestry == "EUR"

    def test_builds_positional_score(self) -> None:
        spec = _spec("PGS005198", multi=True, ancestries=["AFR", "EUR"])
        ws = build_weight_set_from_pgs(
            _pgs_engine(), spec, "body_mass_index", inferred_ancestry="AFR"
        )
        assert ws is not None
        assert ws.snp_count == 2  # guard the all() checks against a vacuous pass
        # No rsIDs → positional weights carry chrom/pos and empty rsid.
        assert all(w.rsid == "" for w in ws.weights)
        assert all(w.chrom and w.pos for w in ws.weights)
        # multi-ancestry covering AFR → source resolved to AFR (no mismatch).
        assert ws.source_ancestry == "AFR"

    def test_absent_score_returns_none(self) -> None:
        spec = _spec("PGS999999", multi=False, ancestries=["EUR"])
        assert build_weight_set_from_pgs(_pgs_engine(), spec, "x") is None

    def test_build_trait_weight_set_end_to_end(self) -> None:
        registry = {
            "body_mass_index": [_spec("PGS005198", multi=True, ancestries=["AFR", "EUR"])],
        }
        ws = build_trait_weight_set(_pgs_engine(), "body_mass_index", "EUR", registry=registry)
        assert ws is not None and ws.pgs_id == "PGS005198"

    def test_build_trait_weight_set_unknown_trait(self) -> None:
        assert build_trait_weight_set(_pgs_engine(), "no_such_trait", "EUR", registry={}) is None


# ── CSA / SAS ancestry-alias coverage (issue #132) ──────────────────────────


class TestCsaSasAlias:
    """The app infers Central/South Asian as ``CSA``; PGS Catalog scores label
    the same South Asian development ancestry ``SAS``. A ``CSA`` sample must be
    treated as covered by a score's ``SAS`` component, not fall through to the
    "any multi-ancestry" branch and get mislabelled as AFR-derived (issue #132).
    """

    # Mirrors the shipped PGS005198 (Smit et al. 2025 BMI PGS) ancestry set.
    _BMI_ANCESTRIES = ["AFR", "AMR", "EAS", "EUR", "SAS"]

    def test_csa_covered_by_sas_multi_ancestry_score(self) -> None:
        specs = [
            _spec("EUR_ONLY", multi=False, ancestries=["EUR"]),
            _spec("BMI_MULTI", multi=True, ancestries=self._BMI_ANCESTRIES),
        ]
        # CSA is covered via the SAS alias → chosen through the covering branch.
        assert select_pgs_for_ancestry(specs, "CSA").pgs_id == "BMI_MULTI"

    def test_csa_resolves_to_csa_not_afr(self) -> None:
        spec = _spec("PGS005198", multi=True, ancestries=self._BMI_ANCESTRIES)
        # Pre-fix this returned "AFR" (ancestries[0]) and tripped a false
        # ancestry-mismatch warning for South Asian samples.
        assert _resolve_source_ancestry(spec, "CSA") == "CSA"

    def test_csa_sample_raises_no_ancestry_mismatch(self) -> None:
        spec = _spec("PGS005198", multi=True, ancestries=self._BMI_ANCESTRIES)
        ws = build_weight_set_from_pgs(
            _pgs_engine(), spec, "body_mass_index", inferred_ancestry="CSA"
        )
        assert ws is not None
        assert ws.source_ancestry == "CSA"
        # Faithfully thread the multi-ancestry provenance the bridge populates,
        # so this exercises the multi-ancestry branch (not the single-ancestry
        # default) — a CSA user is covered by the SAS-labelled dev set via the
        # CSA→SAS alias and must NOT be flagged (issue #239 review regression).
        assert ws.multi_ancestry is True
        assert "SAS" in ws.development_ancestries and "CSA" not in ws.development_ancestries
        result = PRSResult(
            weight_set_name=ws.name,
            trait=ws.trait,
            module=ws.module,
            source_ancestry=ws.source_ancestry,
            source_study=ws.source_study,
            source_pmid=ws.source_pmid,
            sample_size=ws.sample_size,
            raw_score=0.0,
            multi_ancestry=ws.multi_ancestry,
            development_ancestries=list(ws.development_ancestries),
        )
        result = check_ancestry_mismatch(result, "CSA")
        assert result.ancestry_mismatch is False
        assert result.ancestry_warning_text is None

    def test_uncovered_buckets_still_flagged(self) -> None:
        # MID / OCE have no South Asian alias and no component in these scores →
        # they must stay uncovered, so the mismatch fallback is preserved.
        spec = _spec("PGS005198", multi=True, ancestries=self._BMI_ANCESTRIES)
        assert _resolve_source_ancestry(spec, "MID") == "AFR"
        assert _resolve_source_ancestry(spec, "OCE") == "AFR"

    def test_registry_bmi_score_covers_csa(self) -> None:
        # Guard the real shipped registry entry (PGS005198) against regression.
        reg = load_pgs_registry()
        chosen = select_pgs_for_ancestry(reg["body_mass_index"], "CSA")
        assert chosen is not None and chosen.pgs_id == "PGS005198"
        assert _resolve_source_ancestry(chosen, "CSA") == "CSA"

    def test_covers_agrees_with_check_ancestry_mismatch(self) -> None:
        """pgs_bridge selection coverage and prs's warning use ONE rule (#339).

        For each (inferred, dev-set), ``_covers`` must agree with "no ancestry
        mismatch" from ``check_ancestry_mismatch`` on a multi-ancestry result
        built the way the bridge builds it (``source_ancestry`` resolved through
        ``_resolve_source_ancestry``). Centralizing the canonical coverage rule
        in ``ancestry.ancestry_covered`` means these two decisions can no longer
        drift apart (the latent fragility behind the #239 regression).
        """
        cases = [
            ("CSA", ["AFR", "AMR", "EAS", "EUR", "SAS"]),  # covered via CSA→SAS alias
            ("EUR", ["AFR", "EUR", "SAS"]),  # covered directly
            ("SAS", ["AFR", "SAS"]),  # covered directly
            ("MID", ["AFR", "EUR", "SAS"]),  # uncovered (no SA alias)
            ("AFR", ["EUR", "EAS"]),  # uncovered
        ]
        for inferred, dev in cases:
            spec = _spec("PGS", multi=True, ancestries=dev)
            covered = _covers(spec, inferred)
            result = PRSResult(
                weight_set_name="ws",
                trait="t",
                module="m",
                source_ancestry=_resolve_source_ancestry(spec, inferred),
                source_study="s",
                source_pmid="1",
                sample_size=1,
                raw_score=0.0,
                multi_ancestry=True,
                development_ancestries=list(dev),
            )
            result = check_ancestry_mismatch(result, inferred)
            no_mismatch = result.ancestry_mismatch is False
            assert covered == no_mismatch, (
                f"coverage divergence for inferred={inferred}, dev={dev}: "
                f"_covers={covered} but no_mismatch={no_mismatch}"
            )
