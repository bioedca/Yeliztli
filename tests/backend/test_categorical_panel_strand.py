"""Recurrence guard: categorical-panel ref/risk alleles must be strand-consistent.

Every categorical-scoring module (nutrigenomics, gene_health, traits, fitness,
sleep, methylation, skin, allergy) resolves a curated effect by the sample's
genotype via :func:`backend.analysis.genotype_lookup.lookup_by_genotype`, which
tries the reference strand first and the Watson-Crick complement as a fallback.
That fallback rescues a *chip* reporting the opposite strand from the panel, but
it cannot rescue a panel whose own ``risk_allele``/``ref_allele`` sit on
**different** strands from each other: a real heterozygote then matches neither
strand's candidates and the carrier is silently scored the default category.

That is the bug class behind #491 (rs2157719) and #538 (rs4236601 CAV1/CAV2,
where ``ref_allele`` was ``C`` — the complement of the real reference ``G`` —
while ``risk_allele`` was the genuine plus-strand ``A``). The per-locus panel
tests miss it because they exercise homozygotes, which the complement fallback
rescues; only a heterozygote exposes the mixed-strand frame.

This guard pins each curated locus against the SNP's real GRCh37 plus-strand
alleles (``tests/fixtures/categorical_panel_ensembl_alleles.json``, sourced from
Ensembl): ``{risk_allele, ref_allele}`` must be **strand-consistent** with the
real alleles — both plus-strand, or both complements — never mixed. For a locus
that is bi-allelic in Ensembl it additionally asserts the real heterozygote
resolves. Coverage currently spans the loci verified for #538 plus rs2858884
(#608); the fixture is designed to grow to every categorical-panel locus
(follow-up to #538 tracked by #608).

**Known blind spot (#608).** The set-based ``_strand_consistent`` check is
*undecidable* for a locus where every curated allele's Watson–Crick complement is
also a real allele. The flagship case is rs9273363 (HLA-DQB1, real ``C/A/G``): a
mis-stranded panel pair ``{C, T}`` (``T`` = complement of the real ``A``)
complements wholesale to ``{G, A}`` — both real — so the set check FALSE-PASSES,
and being tri-allelic the locus gets no bi-allelic het-resolves check either.
4-allelic loci (e.g. rs4341 ``G/A/C/T``) are trivially undecidable for the same
reason. Such loci need an *observed-genotype-resolves* check (or the scorer's
not-modeled→Indeterminate handling, #608), not the allele-set check;
``test_strand_consistent_blind_spot_for_multiallelic`` characterises the
limitation so it cannot be "fixed" silently. rs9273363 is deliberately NOT in the
fixture and is tracked separately (its T1D risk *direction* is also contested).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.analysis.genotype_lookup import lookup_by_genotype
from backend.analysis.zygosity import COMPLEMENT

PANELS_DIR = Path(__file__).resolve().parent.parent.parent / "backend" / "data" / "panels"
FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "categorical_panel_ensembl_alleles.json"
)

# The modules that score by genotype_effects (see genotype_lookup's docstring).
CATEGORICAL_PANELS = (
    "allergy",
    "fitness",
    "gene_health",
    "methylation",
    "nutrigenomics",
    "skin",
    "sleep",
    "traits",
)


def _iter_snps(obj):
    """Yield every dict carrying both ``rsid`` and ``genotype_effects``.

    Structure-agnostic: panels nest SNPs under varying shapes (flat lists,
    ``pathways[].snps[]``, etc.), so recurse rather than assume a layout.
    """
    if isinstance(obj, dict):
        if "rsid" in obj and "genotype_effects" in obj:
            yield obj
        for value in obj.values():
            yield from _iter_snps(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_snps(item)


def _load_categorical_snps() -> list[tuple[str, str, dict]]:
    """Return ``(panel, rsid, snp_entry)`` for every categorical-panel SNP."""
    out: list[tuple[str, str, dict]] = []
    for panel in CATEGORICAL_PANELS:
        data = json.loads((PANELS_DIR / f"{panel}_panel.json").read_text())
        for snp in _iter_snps(data):
            out.append((panel, snp["rsid"], snp))
    return out


def _load_reference() -> dict[str, dict]:
    ref = json.loads(FIXTURE.read_text())
    ref.pop("_provenance", None)
    return ref


_ALL_SNPS = _load_categorical_snps()
_REFERENCE = _load_reference()

# (panel, rsid, real_alleles, snp_entry) for every categorical SNP whose rsid has
# a verified plus-strand allele reference.
_REFERENCED_CASES = [
    (panel, rsid, _REFERENCE[rsid]["allele_string"].split("/"), snp)
    for (panel, rsid, snp) in _ALL_SNPS
    if rsid in _REFERENCE
]


def _strand_consistent(panel_alleles: set[str], real_alleles: set[str]) -> bool:
    """Whether ``panel_alleles`` sit wholly on one strand of ``real_alleles``.

    True iff every panel allele is a real plus-strand allele, or every panel
    allele is the Watson-Crick complement of a real allele. A pair split across
    strands (one plus, one complement) — the #538 defect — returns False.
    """
    if panel_alleles <= real_alleles:
        return True
    if not panel_alleles <= set(COMPLEMENT):
        return False
    complemented = {COMPLEMENT[a] for a in panel_alleles}
    return complemented <= real_alleles


def test_reference_fixture_is_live() -> None:
    """Every rsid in the reference fixture must still be a real categorical-panel
    locus — so the fixture can't silently rot with stale entries."""
    panel_rsids = {rsid for (_, rsid, _) in _ALL_SNPS}
    stale = sorted(set(_REFERENCE) - panel_rsids)
    assert not stale, f"reference fixture has rsids absent from every categorical panel: {stale}"


# Strand-checkable SNV loci that are deliberately NOT in the fixture, each with a
# documented reason (see the fixture's blind_spot_note). Keep this list tiny.
_FIXTURE_EXCLUSIONS = {
    # Tri-allelic (Ensembl C/A/G) → the set-based strand check is undecidable
    # (every panel allele's complement is also real). Demoted to a non-diagnostic
    # HLA-DQ marker (#731) and covered by an observed-genotype-resolves test in
    # test_gene_health.py instead.
    "rs9273363",
}


def test_strand_fixture_covers_every_snv_locus() -> None:
    """Coverage completeness (#775): every categorical-panel locus whose curated
    ``{risk_allele, ref_allele}`` are single-base A/C/G/T — i.e. a strand-checkable
    SNV — must have an Ensembl reference entry, so a newly-added SNV locus cannot
    silently escape the strand guard. Indel loci (I/D or multi-base risk/ref) are
    out of frame and guarded by the indel-polarity guard instead; the only
    explicit SNV exclusion is the documented tri-allelic ``rs9273363``."""
    snv_loci = set()
    for _, rsid, snp in _ALL_SNPS:
        present = [a for a in (snp.get("risk_allele"), snp.get("ref_allele")) if a]
        if present and all(len(a) == 1 and a in "ACGT" for a in present):
            snv_loci.add(rsid)
    assert snv_loci, "no strand-checkable SNV loci were collected from categorical panels"
    missing = sorted(snv_loci - set(_REFERENCE) - _FIXTURE_EXCLUSIONS)
    assert not missing, (
        "categorical-panel SNV loci missing from the strand reference fixture "
        f"(add one Ensembl GRCh37 lookup each, or document an exclusion): {missing}"
    )


def test_referenced_cases_nonempty() -> None:
    """Guard the guard: collection must actually produce cases (a refactor that
    breaks _iter_snps would otherwise vacuously 'pass')."""
    assert _REFERENCED_CASES, "no referenced categorical-panel loci were collected"
    rsids = {rsid for (_, rsid, _, _) in _REFERENCED_CASES}
    assert "rs4236601" in rsids  # the #538 locus must be under guard


@pytest.mark.parametrize(
    "panel,rsid,real_alleles,snp",
    _REFERENCED_CASES,
    ids=[f"{p}:{r}" for (p, r, _, _) in _REFERENCED_CASES],
)
def test_ref_risk_strand_consistent(
    panel: str, rsid: str, real_alleles: list[str], snp: dict
) -> None:
    """``{risk_allele, ref_allele}`` must be strand-consistent with the SNP's
    real GRCh37 plus-strand alleles (no mixed-strand pair)."""
    panel_alleles = {a for a in (snp.get("risk_allele"), snp.get("ref_allele")) if a}
    assert panel_alleles, f"{panel}:{rsid} declares neither risk_allele nor ref_allele"
    assert _strand_consistent(panel_alleles, set(real_alleles)), (
        f"{panel}:{rsid} alleles {sorted(panel_alleles)} are not strand-consistent with "
        f"Ensembl GRCh37 {'/'.join(real_alleles)} — ref/risk are mixed-strand, so a real "
        f"heterozygote is silently dropped by lookup_by_genotype (#538)."
    )


@pytest.mark.parametrize(
    "panel,rsid,real_alleles,snp",
    [case for case in _REFERENCED_CASES if len(case[2]) == 2],
    ids=[f"{p}:{r}" for (p, r, a, _) in _REFERENCED_CASES if len(a) == 2],
)
def test_real_heterozygote_resolves(
    panel: str, rsid: str, real_alleles: list[str], snp: dict
) -> None:
    """For a locus that is bi-allelic in Ensembl, the heterozygote formed from
    its two real alleles must resolve in ``genotype_effects`` (the carrier-drop
    symptom of the #538 bug, checked directly)."""
    het = real_alleles[0] + real_alleles[1]
    assert lookup_by_genotype(snp["genotype_effects"], het) is not None, (
        f"{panel}:{rsid} real heterozygote {het} (Ensembl {'/'.join(real_alleles)}) does not "
        f"resolve in genotype_effects keys {list(snp['genotype_effects'])} (#538)."
    )


def test_strand_consistent_blind_spot_for_multiallelic() -> None:
    """Characterise the guard's known undecidability (#608).

    ``_strand_consistent`` FALSE-PASSES a mixed-strand pair when every panel allele's
    complement is also a real allele. rs9273363 (HLA-DQB1, real ``C/A/G``) was the
    flagship case: its old mis-stranded panel pair ``{C, T}`` (``T`` = complement of
    the real ``A``) complements wholesale to ``{G, A}`` — both real — so the set
    check reads "consistent" even though ``C`` is plus-strand and ``T`` is not. This
    asserts the guard's LIMITATION, not desired behaviour, using that historical pair
    as the illustration. #731 has since re-keyed rs9273363 to the correct plus-strand
    ``{A, C}`` and demoted it to a non-diagnostic HLA-DQ marker, but it stays out of
    the strand fixture because it is still tri-allelic (the set check still cannot
    validate it); it is covered by an observed-genotype-resolves test instead. If a
    future change makes ``_strand_consistent`` multi-allelic-aware, update this test
    deliberately.
    """
    real = {"C", "A", "G"}  # rs9273363, Ensembl GRCh37 plus strand
    mixed_pair = {"C", "T"}  # the historical mis-stranded pair (T = complement of real A)
    assert _strand_consistent(mixed_pair, real) is True
