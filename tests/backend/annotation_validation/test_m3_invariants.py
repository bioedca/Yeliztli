"""M3 — Property invariants over the live pipeline.

These are assertions that must hold after *any* annotation of *any* sample.
Some are standing regression guards that pass today; the rest encode
post-remediation behaviour and are ``xfail(strict=True)`` until their phase
lands (the strict marker means an early/accidental fix is surfaced immediately).

Invariants are sourced from ``docs/annotation-validation-strategy.md`` §4-M3.
``inv5`` (coverage-bit ⇔ source column) and ``inv8`` (deleterious denominator)
live in ``test_m6_coverage.py`` / ``test_m5_refdata_qa.py`` respectively, where
the supporting fixtures already exist.
"""

from __future__ import annotations

from backend.analysis.zygosity import CARRIED_ZYGOSITIES, classify_zygosity
from tests.backend.annotation_validation.conftest import (
    clinvar_row,
    with_chry_noise_floor,
    with_xx_scaffold,
)

_PATHOGENIC_CATEGORIES = ("clinvar_pathogenic", "ensemble_pathogenic", "rare")


# ── inv1 — no surfaced finding is labelled hom_ref (standing guard) ────────


def test_inv1_no_homref_in_pathogenic_findings(build_live_run) -> None:
    """Every rare/pathogenic finding carries a CARRIED zygosity; none is hom_ref.

    Stronger than a literal-``'hom_ref'`` filter: it also fails on a
    genotype-agnostic regression. A genotype-agnostic engine leaves ``zygosity``
    NULL, which makes the carriage gate drop *every* finding — so asserting the
    het carrier **does** surface (and that every surfaced pathogenic finding has
    a non-NULL CARRIED zygosity) trips on both the hom-ref-leak regression *and*
    the NULL-zygosity regression. (The doc's M3 invariant #1 — "violated 30k+
    times" — is the recomputed-carriage view; this is its live-finding analogue.)
    """
    run = build_live_run(
        variants=[
            {"rsid": "rs_het", "chrom": "7", "pos": 200, "genotype": "GA"},
            {"rsid": "rs_homref", "chrom": "7", "pos": 300, "genotype": "GG"},
        ],
        clinvar=[
            clinvar_row("rs_het", "7", 200, "G", "A", "Pathogenic", 3),
            clinvar_row("rs_homref", "7", 300, "G", "A", "Pathogenic", 3),
        ],
    )
    pathogenic = run.findings_in(*_PATHOGENIC_CATEGORIES)
    # The het carrier must surface — else carriage was never computed and the
    # gate silently zeroed the finding set (the genotype-agnostic regression).
    assert any(f.rsid == "rs_het" for f in pathogenic), (
        "het carrier was not surfaced — zygosity likely NULL (genotype-agnostic)"
    )
    # The hom-ref non-carrier must not surface.
    assert all(f.rsid != "rs_homref" for f in pathogenic)
    # Every surfaced pathogenic finding carries het/hom_alt, never hom_ref/NULL.
    offenders = [f for f in pathogenic if f.zygosity not in CARRIED_ZYGOSITIES]
    assert offenders == [], (
        f"pathogenic findings with non-carried zygosity: "
        f"{[(f.rsid, f.zygosity) for f in offenders]}"
    )


# ── inv2 — every carriable SNV gets a zygosity (F1) ───────────────────────


def test_inv2_snv_with_source_alleles_has_zygosity(build_live_run) -> None:
    """Every annotated SNV that a source supplied ref/alt for has non-NULL zygosity.

    Recomputed against the *known* ClinVar alleles (the annotated ref/alt columns
    are themselves NULL today, so trusting them would mask the bug).
    """
    snv_rsids = {"rs_a": ("G", "A"), "rs_b": ("C", "T"), "rs_c": ("A", "G")}
    run = build_live_run(
        variants=[
            {"rsid": "rs_a", "chrom": "7", "pos": 100, "genotype": "GA"},
            {"rsid": "rs_b", "chrom": "7", "pos": 200, "genotype": "TT"},
            {"rsid": "rs_c", "chrom": "7", "pos": 300, "genotype": "AA"},
        ],
        clinvar=[
            clinvar_row("rs_a", "7", 100, "G", "A", "Pathogenic", 2),
            clinvar_row("rs_b", "7", 200, "C", "T", "Likely pathogenic", 2),
            clinvar_row("rs_c", "7", 300, "A", "G", "Benign", 2),
        ],
        run_analyses=False,
    )
    missing = []
    for rsid in snv_rsids:
        row = run.annotated_by_rsid(rsid)
        assert row is not None, f"{rsid} not annotated"
        if row.zygosity is None:
            missing.append(rsid)
    assert missing == [], f"SNVs left without a zygosity: {missing}"


# ── inv3 — no biallelic genotype is carried for two distinct ALTs (F37) ────


def test_inv3_no_double_carry_at_palindrome() -> None:
    """A biallelic genotype cannot carry two distinct ALTs at one (chrom,pos,ref).

    Property of ``classify_zygosity`` itself. At a palindromic locus the current
    reverse-complement fallback lets a single genotype be 'carried' for both
    ``T`` and a complemented ALT.
    """
    ref = "T"
    genotype = "CC"
    carried_alts = [
        alt
        for alt in ("A", "C", "G")
        if alt != ref and classify_zygosity(genotype, ref, alt) in CARRIED_ZYGOSITIES
    ]
    assert len(carried_alts) <= 1, f"genotype {genotype!r} carries multiple ALTs: {carried_alts}"


# ── inv4 — no chrY/chrX-nonPAR finding contradicts inferred sex (F8) ──────


def test_inv4_no_chry_finding_on_xx(build_live_run) -> None:
    run = build_live_run(
        variants=with_xx_scaffold(
            with_chry_noise_floor(
                {"rsid": "rs_y", "chrom": "Y", "pos": 2_700_000, "genotype": "GG"}
            )
        ),
        clinvar=[clinvar_row("rs_y", "Y", 2_700_000, "A", "G", "Pathogenic", 2, gene="SRY")],
    )
    y_findings = [f for f in run.findings if f.rsid == "rs_y"]
    assert y_findings == []


# ── inv6 — raw reconciles with annotated + an explicit coverage=0 bucket ──


def test_inv6_raw_annotated_reconciliation(build_live_run) -> None:
    """count(raw) == count(annotated) — no variant is silently dropped.

    ``rs_nomatch`` matches no annotation source, so today it is dropped entirely
    (no row, no coverage=0 marker). Post-fix it is written with
    ``annotation_coverage == 0``.
    """
    run = build_live_run(
        variants=[
            {"rsid": "rs_match", "chrom": "7", "pos": 100, "genotype": "GA"},
            {"rsid": "rs_nomatch", "chrom": "7", "pos": 999, "genotype": "CC"},
        ],
        clinvar=[clinvar_row("rs_match", "7", 100, "G", "A", "Pathogenic", 2)],
    )
    n_raw = len(run.raw)
    n_annotated = len(run.annotated)
    n_coverage0 = sum(1 for r in run.annotated if (r.annotation_coverage or 0) == 0)
    assert n_raw == n_annotated, (
        f"raw={n_raw} but annotated={n_annotated} "
        f"(coverage=0 rows={n_coverage0}); a variant was silently dropped"
    )


# ── inv7 — no-call/indel sentinel genotype is never high-confidence (F16) ──


def test_inv7_nocall_indel_not_high_confidence(build_live_run) -> None:
    run = build_live_run(
        variants=[
            {"rsid": "rs_indel", "chrom": "7", "pos": 700, "genotype": "II"},
            {"rsid": "rs_nocall", "chrom": "7", "pos": 800, "genotype": "--"},
        ],
        clinvar=[
            clinvar_row("rs_indel", "7", 700, "ATCT", "A", "Pathogenic", 3),
            clinvar_row("rs_nocall", "7", 800, "G", "A", "Pathogenic", 3),
        ],
    )
    high_conf = [
        f
        for f in run.findings
        if f.rsid in {"rs_indel", "rs_nocall"} and (f.evidence_level or 0) >= 3
    ]
    assert high_conf == [], f"unscoreable genotypes surfaced at evidence>=3: {high_conf}"
