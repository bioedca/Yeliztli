"""Frequency guard: an HLA-proxy locus's risk allele must be the population-MINOR
allele (#877; class of #545/#709/#748).

An HLA tag SNP proxies an *uncommon* HLA haplotype, so its risk / proxy-positive
allele is necessarily the rare (minor) allele. Labelling the common major allele as
``risk`` inverts the dosage and flags the unaffected majority — exactly the #748
(celiac HLA-DQ8 rs7775228, common T mislabelled risk → ~71% of EUR falsely flagged)
defect. This self-discovering guard generalises the per-locus
``TestHLA*ProxyAlleleDirection`` tests in ``test_allergy.py``: it walks every
categorical-panel locus carrying an ``hla_proxy`` block and asserts its
``risk_allele`` is the minor allele, against an offline Ensembl GRCh37
minor-allele/MAF fixture (``categorical_panel_allele_frequencies.json``).

**Why HLA proxies and not every locus.** A frequency guard over *all* categorical
loci is not viable: ~30 of 118 categorical ``risk_allele``s are legitimately the
**major** allele — common-variant GWAS/effect associations (e.g. CLU rs11136000 AD
risk, lactase-persistence, vitamin-D rs10741657, MnSOD rs4880) — which the frequency
signal cannot distinguish from a true inversion (the confirmed #748/#750 inversions
sit at minor-MAF 0.21–0.39, squarely among the legitimate common-risk loci). HLA
proxies are the cleanly-decidable subset: biology *requires* their risk allele to be
the minor one, so "risk == minor" is a sound invariant with no allowlist. (#877)

**Palindromic blind spot.** A palindromic (A/T or C/G) proxy — rs144012689 (#709),
rs1061235 (#545) — is strand-ambiguous: the risk base and its complement are the two
alleles, so an unphased frequency check cannot tell which strand a homozygote is on.
Those loci are handled by the strand-ambiguity-withholding logic + their own
direction tests, not here; this guard skips them (mirrors
``test_categorical_panel_strand.py``'s blind-spot handling).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.analysis.zygosity import COMPLEMENT

PANELS_DIR = Path(__file__).resolve().parent.parent.parent / "backend" / "data" / "panels"
FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "categorical_panel_allele_frequencies.json"
)

# The genotype_effects-scored modules (same set as test_categorical_panel_strand.py).
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
    """Yield every dict carrying ``rsid`` + ``genotype_effects`` (layout-agnostic)."""
    if isinstance(obj, dict):
        if "rsid" in obj and "genotype_effects" in obj:
            yield obj
        for value in obj.values():
            yield from _iter_snps(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_snps(item)


def _hla_proxy_loci() -> list[tuple[str, str, str, str]]:
    """``(panel, rsid, risk_allele, ref_allele)`` for every categorical locus that
    carries an ``hla_proxy`` block and a curated ``{risk, ref}`` pair."""
    out: list[tuple[str, str, str, str]] = []
    for panel in CATEGORICAL_PANELS:
        data = json.loads((PANELS_DIR / f"{panel}_panel.json").read_text())
        for snp in _iter_snps(data):
            if (
                isinstance(snp.get("hla_proxy"), dict)
                and snp.get("risk_allele")
                and snp.get("ref_allele")
            ):
                out.append((panel, snp["rsid"], snp["risk_allele"], snp["ref_allele"]))
    return out


def _load_frequencies() -> dict[str, dict]:
    ref = json.loads(FIXTURE.read_text())
    ref.pop("_provenance", None)
    return ref


def _is_palindromic(a: str, b: str) -> bool:
    return COMPLEMENT.get(a.upper()) == b.upper()


def _risk_tags_minor(risk: str, minor: str) -> bool:
    """Whether the curated (plus-strand) ``risk`` allele is the population-minor allele,
    allowing a whole-strand flip (panel curated on the minus strand)."""
    r, m = risk.upper(), minor.upper()
    return r == m or COMPLEMENT.get(r) == m


_HLA_PROXY_LOCI = _hla_proxy_loci()
_FREQ = _load_frequencies()
# Non-palindromic proxies are frequency-decidable; palindromic ones are the blind spot.
_CHECKABLE = [
    (panel, rsid, risk, ref)
    for (panel, rsid, risk, ref) in _HLA_PROXY_LOCI
    if not _is_palindromic(risk, ref)
]


def test_hla_proxy_loci_discovered() -> None:
    """Sanity: the allergy HLA proxies exist, so the guard can't pass vacuously."""
    assert len(_HLA_PROXY_LOCI) >= 5, f"HLA-proxy discovery regressed: {_HLA_PROXY_LOCI}"
    assert _CHECKABLE, "no non-palindromic HLA proxy to check"


def test_freq_fixture_covers_checkable_hla_proxies() -> None:
    """Every non-palindromic HLA proxy must have a minor-allele entry, so a newly
    added proxy can't be silently skipped — adding one forces an Ensembl fixture row."""
    missing = [
        rsid
        for (_, rsid, _, _) in _CHECKABLE
        if rsid not in _FREQ or not _FREQ[rsid].get("minor_allele")
    ]
    assert not missing, f"add Ensembl GRCh37 minor_allele/MAF to the fixture for: {missing}"


@pytest.mark.parametrize(
    ("panel", "rsid", "risk", "ref"),
    _CHECKABLE,
    ids=[f"{p}:{rsid}" for (p, rsid, _, _) in _CHECKABLE],
)
def test_hla_proxy_risk_allele_is_minor(panel: str, rsid: str, risk: str, ref: str) -> None:
    entry = _FREQ[rsid]
    minor = entry["minor_allele"]
    assert _risk_tags_minor(risk, minor), (
        f"{panel}:{rsid} risk_allele {risk!r} is the population-MAJOR allele "
        f"(minor={minor}, MAF={entry.get('maf')}) — an HLA proxy tags an uncommon "
        "haplotype, so its risk allele must be the minor allele. This is the #748/#709 "
        "inversion class: flip the dosage direction (risk↔ref)."
    )


def test_guard_fires_on_an_inverted_hla_proxy() -> None:
    """Non-vacuity: the guard FIRES when a proxy's risk is the major allele — the exact
    pre-#748 state (rs7775228 risk=T, the ~0.79 major), and passes for the fixed risk=C."""
    minor = _FREQ["rs7775228"]["minor_allele"]
    assert minor == "C"
    assert _risk_tags_minor("C", minor) is True  # fixed direction (#748) passes
    assert _risk_tags_minor("T", minor) is False  # pre-fix inversion fails
