"""M2 — Synthetic truth-set (live path).

One case per hazard class from ``docs/annotation-validation-strategy.md`` §4-M2.
Each builds a tiny reference set, runs the **real** ``run_annotation`` +
``run_all_analyses`` through :func:`build_live_run`, and asserts the resulting
``annotated_variants`` / ``findings`` match the genotype the sample actually
carries.

Every assertion encodes *post-remediation* behaviour. On today's
genotype-agnostic engine they fail (``zygosity`` is NULL, hom-ref/indels/Y-on-XX
are surfaced, multi-allelic picks the highest-star allele), so each is marked
``xfail(strict=True)`` tagged to its finding id. The marker is removed in the
phase that lands the fix — at which point the test becomes a live gate.

The negative controls (hom-ref ⇒ no pathogenic finding) are the most important:
they would have failed on day one.
"""

from __future__ import annotations

from tests.backend.annotation_validation.conftest import with_chry_noise_floor, with_xx_scaffold


def _clinvar(rsid, chrom, pos, ref, alt, sig, stars, *, gene="GENEX"):
    return {
        "rsid": rsid,
        "chrom": chrom,
        "pos": pos,
        "ref": ref,
        "alt": alt,
        "significance": sig,
        "review_stars": stars,
        "accession": f"VCV_{rsid}",
        "conditions": f"{gene}-related condition",
        "gene_symbol": gene,
        "variation_id": abs(hash(rsid)) % 1_000_000,
    }


# ── F3/F6: hom-ref negative control ───────────────────────────────────────


def test_homref_pathogenic_snv_not_surfaced(build_live_run) -> None:
    """A hom-ref call at a ClinVar P/LP SNV must surface NO finding."""
    run = build_live_run(
        variants=[{"rsid": "rs_homref", "chrom": "7", "pos": 100, "genotype": "GG"}],
        clinvar=[_clinvar("rs_homref", "7", 100, "G", "A", "Pathogenic", 3)],
    )
    annotated = run.annotated_by_rsid("rs_homref")
    assert annotated is not None and annotated.zygosity == "hom_ref"
    assert run.findings_for_rsid("rs_homref") == []


# ── F3/F7: het carrier ────────────────────────────────────────────────────


def test_het_carrier_zygosity(build_live_run) -> None:
    """A het carrier: zygosity=het AND the finding surfaces end-to-end.

    Drives the full ``run_all_analyses`` so the *positive control* is exercised —
    the carriage gate must **admit** carriers, not merely exclude hom-ref. (M2
    §4 requires the actual ``run_annotation + run_all_analyses`` path, and a
    genotype-agnostic regression would zero the gate, dropping this finding.)
    """
    run = build_live_run(
        variants=[{"rsid": "rs_het", "chrom": "7", "pos": 200, "genotype": "GA"}],
        clinvar=[_clinvar("rs_het", "7", 200, "G", "A", "Pathogenic", 3)],
    )
    annotated = run.annotated_by_rsid("rs_het")
    assert annotated is not None
    assert annotated.zygosity == "het"
    assert any(f.category == "clinvar_pathogenic" for f in run.findings_for_rsid("rs_het")), (
        "het carrier of a Pathogenic SNV did not surface a finding"
    )


# ── F3/F7: hom-alt ────────────────────────────────────────────────────────


def test_hom_alt_zygosity(build_live_run) -> None:
    """A hom-alt carrier: zygosity=hom_alt AND the finding surfaces end-to-end."""
    run = build_live_run(
        variants=[{"rsid": "rs_homalt", "chrom": "7", "pos": 300, "genotype": "AA"}],
        clinvar=[_clinvar("rs_homalt", "7", 300, "G", "A", "Likely pathogenic", 2)],
    )
    annotated = run.annotated_by_rsid("rs_homalt")
    assert annotated is not None
    assert annotated.zygosity == "hom_alt"
    assert any(f.category == "clinvar_pathogenic" for f in run.findings_for_rsid("rs_homalt")), (
        "hom-alt carrier of a Likely-pathogenic SNV did not surface a finding"
    )


# ── F17: reverse-strand het ───────────────────────────────────────────────


def test_reverse_strand_het(build_live_run) -> None:
    """Genotype on the opposite strand still resolves to the carried zygosity."""
    run = build_live_run(
        # ClinVar G>A; the sample reports the reverse-strand complement C/T.
        variants=[{"rsid": "rs_revstrand", "chrom": "7", "pos": 400, "genotype": "CT"}],
        clinvar=[_clinvar("rs_revstrand", "7", 400, "G", "A", "Pathogenic", 2)],
    )
    annotated = run.annotated_by_rsid("rs_revstrand")
    assert annotated is not None
    assert annotated.zygosity == "het"
    assert any(
        f.category == "clinvar_pathogenic" for f in run.findings_for_rsid("rs_revstrand")
    ), "reverse-strand het carrier did not surface a finding"


# ── F37: palindromic site, vendor declares + strand ───────────────────────


def test_palindromic_homozygous_ancestrydna(build_live_run) -> None:
    """At a palindromic A/T site an AncestryDNA (+ strand) hom call is hom_alt."""
    run = build_live_run(
        file_format="ancestrydna_v2.0",
        variants=[{"rsid": "rs_palin", "chrom": "7", "pos": 500, "genotype": "TT"}],
        clinvar=[_clinvar("rs_palin", "7", 500, "A", "T", "Pathogenic", 2)],
    )
    annotated = run.annotated_by_rsid("rs_palin")
    assert annotated is not None
    assert annotated.zygosity == "hom_alt"
    assert any(f.category == "clinvar_pathogenic" for f in run.findings_for_rsid("rs_palin")), (
        "palindromic hom-alt carrier did not surface a finding"
    )


# ── F10: multi-allelic, carry ALT#2 ───────────────────────────────────────


def test_multiallelic_picks_carried_allele(build_live_run) -> None:
    """Genotype carries the lower-star ALT; its significance must win."""
    run = build_live_run(
        variants=[{"rsid": "rs_multi", "chrom": "7", "pos": 600, "genotype": "TT"}],
        clinvar=[
            # C>G Pathogenic (2★) is the highest-star row but NOT carried.
            _clinvar("rs_multi", "7", 600, "C", "G", "Pathogenic", 2),
            # C>T Likely benign (1★) IS the carried allele.
            _clinvar("rs_multi", "7", 600, "C", "T", "Likely benign", 1),
        ],
    )
    annotated = run.annotated_by_rsid("rs_multi")
    assert annotated is not None
    assert annotated.clinvar_significance == "Likely benign"
    assert annotated.zygosity == "hom_alt"
    # ...and therefore it is NOT surfaced as a pathogenic finding.
    assert run.findings_in("clinvar_pathogenic") == [] or all(
        f.rsid != "rs_multi" for f in run.findings_in("clinvar_pathogenic")
    )


# ── F11: dbNSFP multi-allelic — scores must come from the carried ALT ─────


def test_multiallelic_dbnsfp_uses_carried_alt(build_live_run) -> None:
    """At a multi-allelic dbNSFP site the carried ALT's scores must be stored.

    Two ``dbnsfp_scores`` rows exist for the rsid — a benign ALT=A and a
    deleterious ALT=C. The old loader kept whichever row SQLite returned last,
    so the stored scores (and ``ensemble_pathogenic``) could come from an ALT
    the sample does not carry (F11). Genotype ``CC`` carries G>C, so the
    deleterious C-row must win. Crucially the carried ALT (C) sorts *after* the
    non-carried ALT (A), so a mere deterministic-fallback (lowest-ALT) pick
    would choose the wrong (benign A) row — only genuine carriage-based
    selection lands on C.
    """
    base = {"#chr": "7", "pos(1-based)": "100", "ref": "G", "rs_dbSNP": "rs_mdb"}
    run = build_live_run(
        variants=[{"rsid": "rs_mdb", "chrom": "7", "pos": 100, "genotype": "CC"}],
        dbnsfp_rows=[
            # Non-carried ALT (A), sorts first: uniformly benign.
            {
                **base,
                "alt": "A",
                "CADD_phred": "1.0",
                "SIFT4G_score": "0.9",
                "Polyphen2_HVAR_score": "0.01",
                "REVEL_score": "0.05",
                "MetaSVM_score": "0.0",
            },
            # Carried ALT (C): uniformly deleterious → ensemble_pathogenic.
            {
                **base,
                "alt": "C",
                "CADD_phred": "33.0",
                "SIFT4G_score": "0.0",
                "Polyphen2_HVAR_score": "0.99",
                "REVEL_score": "0.95",
                "MetaSVM_score": "0.9",
            },
        ],
        run_analyses=False,
    )
    row = run.annotated_by_rsid("rs_mdb")
    assert row is not None
    # The carried (C) row's CADD must be stored, not the non-carried (A) row's.
    assert abs((row.cadd_phred or 0.0) - 33.0) < 1e-6, (
        f"expected carried-ALT CADD 33.0, got {row.cadd_phred}"
    )
    assert row.ensemble_pathogenic, "carried deleterious ALT should be ensemble_pathogenic"


# ── F16: indel (I/D) is unscoreable, not confident-Pathogenic ─────────────


def test_indel_not_confident_pathogenic(build_live_run) -> None:
    run = build_live_run(
        variants=[{"rsid": "rs_indel", "chrom": "7", "pos": 700, "genotype": "II"}],
        clinvar=[_clinvar("rs_indel", "7", 700, "ATCT", "A", "Pathogenic", 3)],
    )
    high_conf = [
        f
        for f in run.findings_for_rsid("rs_indel")
        if f.category == "clinvar_pathogenic" and (f.evidence_level or 0) >= 3
    ]
    assert high_conf == []


# ── F6/F7: monogenic clinical modules surface carriers (un-suppression) ────


def test_monogenic_cancer_finding_surfaces_for_carrier(build_live_run) -> None:
    """A het carrier of a panel cancer-gene P/LP variant surfaces a cancer finding.

    This is the *false-negative* half of the core defect (F6/F7): cancer /
    cardiovascular / carrier modules gate on ``zygosity IN CARRIED_ZYGOSITIES``
    (cancer.py), so a genotype-agnostic engine (NULL zygosity) silently
    suppresses **all** of them — the most clinically actionable surface. The
    ``module="cancer"`` finding only appears once carriage is wired (C1) and the
    gene symbol is attached (VEP). The hom-ref control proves the gate still
    excludes non-carriers.
    """
    run = build_live_run(
        variants=[
            # het carrier of a BRCA1-panel P/LP variant — must surface
            {"rsid": "rs80357906", "chrom": "17", "pos": 43_094_000, "genotype": "GA"},
            # hom-ref at another BRCA1-panel P/LP variant — must NOT surface
            {"rsid": "rs80357783", "chrom": "17", "pos": 43_095_000, "genotype": "GG"},
        ],
        clinvar=[
            _clinvar("rs80357906", "17", 43_094_000, "G", "A", "Pathogenic", 3, gene="BRCA1"),
            _clinvar("rs80357783", "17", 43_095_000, "G", "A", "Pathogenic", 3, gene="BRCA1"),
        ],
        vep=[
            {
                "rsid": "rs80357906",
                "chrom": "17",
                "pos": 43_094_000,
                "ref": "G",
                "alt": "A",
                "gene_symbol": "BRCA1",
                "consequence": "stop_gained",
            },
            {
                "rsid": "rs80357783",
                "chrom": "17",
                "pos": 43_095_000,
                "ref": "G",
                "alt": "A",
                "gene_symbol": "BRCA1",
                "consequence": "stop_gained",
            },
        ],
    )
    cancer_rsids = {f.rsid for f in run.findings_for_module("cancer")}
    assert "rs80357906" in cancer_rsids, (
        "het carrier of a cancer-panel P/LP variant was suppressed (F6/F7)"
    )
    assert "rs80357783" not in cancer_rsids, "hom-ref non-carrier leaked into cancer findings"


# ── F18: merged rsid resolved to its current id ───────────────────────────


def test_merged_rsid_resolved(build_live_run) -> None:
    """An old rsid whose ClinVar record lives under the current id is annotated."""
    run = build_live_run(
        variants=[{"rsid": "rs_old", "chrom": "1", "pos": 800, "genotype": "AA"}],
        clinvar=[_clinvar("rs_new", "1", 800, "G", "A", "Pathogenic", 2, gene="GBA")],
        dbsnp_merge_rows=[{"old_rsid": "rs_old", "current_rsid": "rs_new", "build_id": 151}],
    )
    annotated = run.annotated_by_rsid("rs_old")
    assert annotated is not None
    assert annotated.clinvar_significance == "Pathogenic"
    assert annotated.zygosity == "hom_alt"
    # ...and the recovered Pathogenic call surfaces a finding (end-to-end gate).
    assert any(f.category == "clinvar_pathogenic" for f in run.findings_for_rsid("rs_old")), (
        "recovered merged-rsid carrier did not surface a finding"
    )


# ── F8: chrY finding on an XX sample is biologically impossible ────────────


def test_no_chry_finding_on_xx_sample(build_live_run) -> None:
    run = build_live_run(
        variants=with_xx_scaffold(
            with_chry_noise_floor(
                {"rsid": "rs_y", "chrom": "Y", "pos": 2_700_000, "genotype": "GG"}
            )
        ),
        clinvar=[_clinvar("rs_y", "Y", 2_700_000, "A", "G", "Pathogenic", 2, gene="SRY")],
    )
    assert run.findings_for_rsid("rs_y") == []
