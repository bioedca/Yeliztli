"""Tests for the SW-C6 imputed common-variant ClinVar finding source.

Covers the safety posture that makes imputed P/LP findings defensible:
graceful degradation (no imputation → no findings), the firewall re-asserted at
the finding gate, carriage taken from the imputed GT best-guess genotype,
exact-allele ClinVar matching, exclusion of chip-typed loci, the evidence-level
cap, and the imputed-not-typed label + confirm-clinically caveat. Mirrors the
in-memory SQLite fixture pattern used across the backend suite.
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from backend.analysis.imputed_findings import (
    IMPUTED_CLINVAR_PATHOGENIC_CATEGORY,
    IMPUTED_CONFIRMATION_CAVEAT,
    IMPUTED_EVIDENCE_CAP,
    IMPUTED_MODULE,
    _minimal_repr,
    find_imputed_clinvar_findings,
    store_imputed_findings,
)
from backend.db.tables import (
    annotated_variants,
    clinvar_variants,
    findings,
    imputed_variants,
)

# A real common Pathogenic SNV: HFE C282Y rs1800562 (GRCh37 chr6:26093141 G>A),
# MAF ~5% in Europeans — exactly the common-but-clinical locus an array can miss
# and imputation can recover.
HFE = {"chrom": "6", "pos": 26093141, "ref": "G", "alt": "A"}


def _imp(
    *,
    dr2: float = 0.95,
    af: float = 0.05,
    dosage: float | None = 1.0,
    best_guess_copies: int | None = 1,
    **over,
) -> dict:
    """An ``imputed_variants`` row (defaults: HFE C282Y, well-imputed, common, het)."""
    return {
        **HFE,
        "dr2": dr2,
        "af": af,
        "dosage": dosage,
        "best_guess_copies": best_guess_copies,
        **over,
    }


def _cv(
    *,
    significance: str = "Pathogenic",
    review_stars: int = 2,
    rsid: str = "rs1800562",
    gene_symbol: str = "HFE",
    accession: str = "VCV000009146",
    conditions: str = "Hereditary hemochromatosis",
    **over,
) -> dict:
    """A ``clinvar_variants`` row (defaults: HFE C282Y Pathogenic 2★)."""
    return {
        **HFE,
        "rsid": rsid,
        "significance": significance,
        "review_stars": review_stars,
        "accession": accession,
        "conditions": conditions,
        "gene_symbol": gene_symbol,
        "variation_id": 9146,
        **over,
    }


def _seed_imputed(engine: sa.Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(imputed_variants), rows)


def _seed_typed(engine: sa.Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(annotated_variants), rows)


def _seed_clinvar(engine: sa.Engine, rows: list[dict]) -> None:
    with engine.begin() as conn:
        conn.execute(sa.insert(clinvar_variants), rows)


# ── Graceful degradation ─────────────────────────────────────────────────


def test_no_imputation_returns_empty(
    sample_engine: sa.Engine, reference_engine: sa.Engine
) -> None:
    """Empty imputed_variants → no findings (byte-identical to not running)."""
    _seed_clinvar(reference_engine, [_cv()])
    assert find_imputed_clinvar_findings(sample_engine, reference_engine) == []
    assert store_imputed_findings([], sample_engine) == 0


def test_missing_table_is_graceful(sample_engine: sa.Engine, reference_engine: sa.Engine) -> None:
    """A sample DB predating Wave C (no imputed_variants table) does not crash."""
    imputed_variants.drop(sample_engine)
    _seed_clinvar(reference_engine, [_cv()])
    assert find_imputed_clinvar_findings(sample_engine, reference_engine) == []


# ── The core uplift: a carried, imputed, common Pathogenic SNV the chip missed ──


def test_surfaces_imputed_pathogenic(
    sample_engine: sa.Engine, reference_engine: sa.Engine
) -> None:
    _seed_imputed(sample_engine, [_imp()])
    _seed_clinvar(reference_engine, [_cv()])

    results = find_imputed_clinvar_findings(sample_engine, reference_engine)
    assert len(results) == 1
    f = results[0]
    assert (f.chrom, f.pos, f.ref, f.alt) == ("6", 26093141, "G", "A")
    assert f.zygosity == "het"
    assert f.clinvar_significance == "Pathogenic"
    assert f.evidence_level <= IMPUTED_EVIDENCE_CAP

    n = store_imputed_findings(results, sample_engine)
    assert n == 1
    with sample_engine.connect() as conn:
        rows = conn.execute(sa.select(findings).where(findings.c.module == IMPUTED_MODULE)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.category == IMPUTED_CLINVAR_PATHOGENIC_CATEGORY
    assert row.evidence_level <= IMPUTED_EVIDENCE_CAP
    assert IMPUTED_CONFIRMATION_CAVEAT in row.finding_text
    detail = json.loads(row.detail_json)
    assert detail["imputed"] is True
    assert detail["dr2"] == 0.95
    assert detail["copies"] == 1
    assert "33589468" in json.loads(row.pmid_citations)


def test_best_guess_hom_alt_sets_zygosity(
    sample_engine: sa.Engine, reference_engine: sa.Engine
) -> None:
    _seed_imputed(sample_engine, [_imp(dosage=1.2, best_guess_copies=2)])
    _seed_clinvar(reference_engine, [_cv()])
    results = find_imputed_clinvar_findings(sample_engine, reference_engine)
    assert len(results) == 1
    assert results[0].zygosity == "hom_alt"
    assert results[0].copies == 2
    assert results[0].dosage == 1.2


# ── Firewall enforced at the gate (defense in depth) ──────────────────────


def test_firewall_quarantines_low_dr2(
    sample_engine: sa.Engine, reference_engine: sa.Engine
) -> None:
    """A low-DR2 imputed row (should never persist) is still dropped at the gate."""
    _seed_imputed(sample_engine, [_imp(dr2=0.5)])
    _seed_clinvar(reference_engine, [_cv()])
    assert find_imputed_clinvar_findings(sample_engine, reference_engine) == []


def test_firewall_quarantines_rare(sample_engine: sa.Engine, reference_engine: sa.Engine) -> None:
    """A rare (MAF < 1%) imputed row is dropped even at high DR2."""
    _seed_imputed(sample_engine, [_imp(af=0.005)])
    _seed_clinvar(reference_engine, [_cv()])
    assert find_imputed_clinvar_findings(sample_engine, reference_engine) == []


# ── Carriage comes from GT best-guess genotype, not rounded DS ─────────────


def test_best_guess_hom_reference_not_a_carrier(
    sample_engine: sa.Engine, reference_engine: sa.Engine
) -> None:
    _seed_imputed(sample_engine, [_imp(dosage=1.0, best_guess_copies=0)])
    _seed_clinvar(reference_engine, [_cv()])
    assert find_imputed_clinvar_findings(sample_engine, reference_engine) == []


def test_missing_best_guess_not_a_carrier(
    sample_engine: sa.Engine, reference_engine: sa.Engine
) -> None:
    _seed_imputed(sample_engine, [_imp(dosage=1.8, best_guess_copies=None)])
    _seed_clinvar(reference_engine, [_cv()])
    assert find_imputed_clinvar_findings(sample_engine, reference_engine) == []


def test_gt_het_surfaces_when_dosage_would_round_to_zero(
    sample_engine: sa.Engine, reference_engine: sa.Engine
) -> None:
    _seed_imputed(sample_engine, [_imp(dosage=0.49, best_guess_copies=1)])
    _seed_clinvar(reference_engine, [_cv()])
    results = find_imputed_clinvar_findings(sample_engine, reference_engine)
    assert len(results) == 1
    assert results[0].zygosity == "het"
    assert results[0].copies == 1
    assert results[0].dosage == 0.49


def test_dosage_half_boundary_does_not_create_carrier(
    sample_engine: sa.Engine, reference_engine: sa.Engine
) -> None:
    _seed_imputed(sample_engine, [_imp(dosage=0.5, best_guess_copies=0)])
    _seed_clinvar(reference_engine, [_cv()])
    assert find_imputed_clinvar_findings(sample_engine, reference_engine) == []


def test_missing_dosage_with_gt_carriage_still_surfaces(
    sample_engine: sa.Engine, reference_engine: sa.Engine
) -> None:
    _seed_imputed(sample_engine, [_imp(dosage=None, best_guess_copies=1)])
    _seed_clinvar(reference_engine, [_cv()])
    results = find_imputed_clinvar_findings(sample_engine, reference_engine)
    assert len(results) == 1
    assert results[0].dosage is None


def test_legacy_table_without_best_guess_withholds_instead_of_rounding_ds(
    sample_engine: sa.Engine, reference_engine: sa.Engine
) -> None:
    imputed_variants.drop(sample_engine)
    with sample_engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE imputed_variants ("
                "chrom TEXT NOT NULL, pos INTEGER NOT NULL, ref TEXT NOT NULL, "
                "alt TEXT NOT NULL, dr2 REAL NOT NULL, af REAL NOT NULL, dosage REAL, "
                "PRIMARY KEY (chrom, pos, alt))"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO imputed_variants "
                "(chrom, pos, ref, alt, dr2, af, dosage) "
                "VALUES ('6', 26093141, 'G', 'A', 0.95, 0.05, 1.8)"
            )
        )
    _seed_clinvar(reference_engine, [_cv()])
    assert find_imputed_clinvar_findings(sample_engine, reference_engine) == []


# ── Exact-allele match + no duplication of typed alleles ──────────────────


def test_allele_mismatch_no_finding(sample_engine: sa.Engine, reference_engine: sa.Engine) -> None:
    """Imputed ALT differs from the ClinVar record's ALT at the same position → skip."""
    _seed_imputed(sample_engine, [_imp(alt="T")])
    _seed_clinvar(reference_engine, [_cv(alt="A")])
    assert find_imputed_clinvar_findings(sample_engine, reference_engine) == []


def test_typed_allele_excluded(sample_engine: sa.Engine, reference_engine: sa.Engine) -> None:
    """The *same* allele the chip directly typed is owned by the typed generators —
    excluded so the imputed layer doesn't duplicate it."""
    _seed_imputed(sample_engine, [_imp()])  # imputed HFE G>A
    _seed_typed(
        sample_engine,
        [{"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "ref": "G", "alt": "A"}],
    )
    _seed_clinvar(reference_engine, [_cv()])
    assert find_imputed_clinvar_findings(sample_engine, reference_engine) == []


def test_typed_different_allele_does_not_suppress(
    sample_engine: sa.Engine, reference_engine: sa.Engine
) -> None:
    """A typed *different* ALT at the same coordinate must not suppress a firewall-cleared
    imputed exact ClinVar P/LP allele (issue #1187).

    ClinVar classifications are allele-specific (Landrum 2016, PMID:26582918), and the
    typed generators own only the allele the chip typed — here G>A. A separate imputed
    G>C that exactly matches its own ClinVar Pathogenic record is a genuine chip gap, so
    it must still surface despite sharing the coordinate with the typed G>A."""
    _seed_imputed(sample_engine, [_imp(alt="C")])  # imputed G>C (chip missed this allele)
    _seed_typed(
        sample_engine,
        [{"rsid": "rs1800562", "chrom": "6", "pos": 26093141, "ref": "G", "alt": "A"}],
    )
    _seed_clinvar(
        reference_engine,
        [_cv(alt="C", accession="VCV000000001", rsid="rs1800562")],
    )

    results = find_imputed_clinvar_findings(sample_engine, reference_engine)
    assert len(results) == 1
    f = results[0]
    assert (f.chrom, f.pos, f.ref, f.alt) == ("6", 26093141, "G", "C")
    assert f.clinvar_significance == "Pathogenic"
    assert f.evidence_level <= IMPUTED_EVIDENCE_CAP


def test_minimal_repr_is_noop_for_snvs_and_trims_indels() -> None:
    """`_minimal_repr` leaves SNVs untouched and reduces equivalent indel spellings
    to one canonical parsimonious form (issue #1218)."""
    # SNVs: nothing to trim → unchanged (the dominant, must-not-regress path).
    assert _minimal_repr("1", 100, "G", "A") == ("1", 100, "G", "A")
    assert _minimal_repr("1", 100, "C", "T") == ("1", 100, "C", "T")
    # Empty/missing allele → returned as-is (cannot claim a specific allele).
    assert _minimal_repr("1", 100, "", "") == ("1", 100, "", "")
    # The same one-base deletion in two valid VCF spellings collapses to one form.
    assert _minimal_repr("1", 100, "GA", "G") == _minimal_repr("1", 100, "GAA", "GA")
    assert _minimal_repr("1", 100, "GAA", "GA") == ("1", 100, "GA", "G")
    # A trailing-context-only difference is position-preserving.
    assert _minimal_repr("1", 100, "ATT", "A") == _minimal_repr("1", 100, "ATTT", "AT")
    # Shared suffix then shared prefix: the coordinate advances as the prefix is trimmed.
    assert _minimal_repr("1", 100, "ATCG", "ATG") == ("1", 101, "TC", "T")


def test_typed_indel_excludes_equivalent_imputed_indel_representation(
    sample_engine: sa.Engine, reference_engine: sa.Engine
) -> None:
    """A typed indel excludes an imputed indel written with a different but
    parsimony-equivalent anchor/trailing context (issue #1218).

    Without minimal-representation matching the two spellings differ as raw strings,
    so the chip-typed indel fails to suppress the imputed one and a duplicate
    imputed-not-typed finding surfaces for a variant the chip already has.
    """
    # Imputed deletion at 1:100 spelled GAA>GA; ClinVar carries that exact spelling,
    # so absent the typed-exclusion it would surface a finding.
    _seed_imputed(sample_engine, [_imp(chrom="1", pos=100, ref="GAA", alt="GA")])
    _seed_clinvar(
        reference_engine,
        [_cv(chrom="1", pos=100, ref="GAA", alt="GA", rsid="rsINDEL", accession="VCV000000002")],
    )
    # The chip directly typed the SAME deletion, minimally spelled GA>G at 1:100.
    _seed_typed(
        sample_engine,
        [{"rsid": "rsINDEL", "chrom": "1", "pos": 100, "ref": "GA", "alt": "G"}],
    )

    # Equivalent under minimal representation → excluded, no duplicate finding.
    assert find_imputed_clinvar_findings(sample_engine, reference_engine) == []


def test_typed_different_indel_does_not_exclude_imputed_indel(
    sample_engine: sa.Engine, reference_engine: sa.Engine
) -> None:
    """A typed indel that is NOT the same variant must not suppress an imputed indel
    (the minimal-representation match stays allele-specific, issue #1218)."""
    _seed_imputed(sample_engine, [_imp(chrom="1", pos=100, ref="GAA", alt="GA")])
    _seed_clinvar(
        reference_engine,
        [_cv(chrom="1", pos=100, ref="GAA", alt="GA", rsid="rsINDEL", accession="VCV000000002")],
    )
    # A genuinely different deletion at the same coordinate (CT>C) — not equivalent.
    _seed_typed(
        sample_engine,
        [{"rsid": "rsOTHER", "chrom": "1", "pos": 100, "ref": "CT", "alt": "C"}],
    )

    results = find_imputed_clinvar_findings(sample_engine, reference_engine)
    assert len(results) == 1
    assert (results[0].ref, results[0].alt) == ("GAA", "GA")


def test_chrom_prefix_normalized(sample_engine: sa.Engine, reference_engine: sa.Engine) -> None:
    """``chr6`` (imputed) matches ``6`` (ClinVar) — chr-prefix normalized both sides."""
    _seed_imputed(sample_engine, [_imp(chrom="chr6")])
    _seed_clinvar(reference_engine, [_cv(chrom="6")])
    assert len(find_imputed_clinvar_findings(sample_engine, reference_engine)) == 1


# ── Only ordinary high-penetrance P/LP surfaces ───────────────────────────


def test_compound_pathogenic_matches(
    sample_engine: sa.Engine, reference_engine: sa.Engine
) -> None:
    """A ``Pathogenic|drug response`` compound is still primary-pathogenic (#813)."""
    _seed_imputed(sample_engine, [_imp()])
    _seed_clinvar(reference_engine, [_cv(significance="Pathogenic|drug response")])
    assert len(find_imputed_clinvar_findings(sample_engine, reference_engine)) == 1


def test_non_pathogenic_excluded(sample_engine: sa.Engine, reference_engine: sa.Engine) -> None:
    for significance in (
        "Benign",
        "Uncertain significance",
        "Conflicting classifications of pathogenicity",
        "Pathogenic, low penetrance",  # distinct lower-penetrance tier (#987)
    ):
        with sample_engine.begin() as conn:
            conn.execute(sa.delete(imputed_variants))
        with reference_engine.begin() as conn:
            conn.execute(sa.delete(clinvar_variants))
        _seed_imputed(sample_engine, [_imp()])
        _seed_clinvar(reference_engine, [_cv(significance=significance)])
        assert find_imputed_clinvar_findings(sample_engine, reference_engine) == [], significance


def test_evidence_level_capped(sample_engine: sa.Engine, reference_engine: sa.Engine) -> None:
    """A 3★ Pathogenic (evidence 4 if typed) is capped at the imputed ceiling."""
    _seed_imputed(sample_engine, [_imp()])
    _seed_clinvar(reference_engine, [_cv(review_stars=3)])
    results = find_imputed_clinvar_findings(sample_engine, reference_engine)
    assert len(results) == 1
    assert results[0].evidence_level == IMPUTED_EVIDENCE_CAP


# ── Store replace semantics ───────────────────────────────────────────────


def test_store_replaces_not_accumulates(
    sample_engine: sa.Engine, reference_engine: sa.Engine
) -> None:
    _seed_imputed(sample_engine, [_imp()])
    _seed_clinvar(reference_engine, [_cv()])
    results = find_imputed_clinvar_findings(sample_engine, reference_engine)
    store_imputed_findings(results, sample_engine)
    store_imputed_findings(results, sample_engine)  # re-run
    with sample_engine.connect() as conn:
        count = conn.execute(
            sa.select(sa.func.count())
            .select_from(findings)
            .where(findings.c.module == IMPUTED_MODULE)
        ).scalar()
    assert count == 1


def test_store_empty_clears_prior(sample_engine: sa.Engine, reference_engine: sa.Engine) -> None:
    _seed_imputed(sample_engine, [_imp()])
    _seed_clinvar(reference_engine, [_cv()])
    store_imputed_findings(
        find_imputed_clinvar_findings(sample_engine, reference_engine), sample_engine
    )
    # A later run with no imputed findings clears the stale module rows.
    assert store_imputed_findings([], sample_engine) == 0
    with sample_engine.connect() as conn:
        count = conn.execute(
            sa.select(sa.func.count())
            .select_from(findings)
            .where(findings.c.module == IMPUTED_MODULE)
        ).scalar()
    assert count == 0


# ── Orchestration wiring ──────────────────────────────────────────────────


def test_wired_into_run_all() -> None:
    """run_all registers the imputed-variants module so it runs post-annotation."""
    from backend.analysis.run_all import _get_modules

    assert "imputed_variants" in {name for name, _ in _get_modules()}
