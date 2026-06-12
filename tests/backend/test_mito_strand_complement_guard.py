"""Guard: mitochondrial risk loci must disable the strand-complement fallback.

The by-rsID risk-genotype engine (``backend/analysis/risk_genotype.py``) canonical-
izes an observed genotype into the ``{ref, risk}`` frame by trying the reference
strand first and then, as a fallback, the **Watson–Crick complement** — the chip
probe reported on the reverse strand (``risk_dosage`` / ``canonical_alleles`` in
``backend/analysis/allele_match.py``). That reverse-strand fallback is correct for
nuclear, diploid loci, where a genotyping array may report either strand for the
same biallelic SNP, so it defaults on (``RiskLocus.allow_strand_complement = True``).

It is **wrong for mitochondrial DNA**. The human mtDNA genome is high-copy,
strictly maternally inherited and overwhelmingly *homoplasmic*, with disease
variants defined against a single reference strand — the revised Cambridge
Reference Sequence (rCRS). There is no diploid two-strand probe ambiguity to
harmonize: a single plus-frame base that merely *complements* the rCRS risk
allele is a **different mtDNA position/variant**, not a strand flip of the same
allele. Accepting the complement therefore manufactures a false-positive risk
call. (Background: mtDNA is high-copy, homoplasmic and maternally inherited;
variants are called against the rCRS — see #30/#31 and the panel descriptions.)

Issues #30 (MT-RNR1 aminoglycoside ototoxicity) and #31 (LHON primary mutations)
fixed exactly this defect by setting ``"allow_strand_complement": false`` on every
mtDNA locus. This guard pins that invariant so it cannot silently regress: a new
mitochondrial locus added to any ``category: "risk_genotype"`` panel that forgets
the flag (and so inherits the ``True`` default in ``load_risk_panel``) fails CI
here rather than shipping a complement-fallback false positive.

This is the mitochondrial-strand analogue of the diplotype-coverage guard (#59)
and of ``test_cpic_allele_strand.py``. Follow-up to #30 / #31 (issue #87).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_PANELS_DIR = Path(__file__).resolve().parents[2] / "backend" / "data" / "panels"

# mtDNA HGVS reference prefix — ``m.<pos><ref>><alt>`` (e.g. ``m.1555A>G``).
# Anchored so it matches mitochondrial coordinates without catching protein/cDNA
# substrings (``p.Met...``, ``c.100C>T``) that would falsely flag a nuclear locus.
_MITO_LABEL_RE = re.compile(r"^m\.\d")

# Every mitochondrial risk locus currently in the tree (lhon + mt_rnr1, fixed by
# #30/#31). The guard must keep *discovering* these — see
# ``test_guard_discovers_known_mito_loci`` — so it can never pass vacuously.
_KNOWN_MITO_RSIDS = frozenset(
    {
        "rs199476112",  # LHON MT-ND4  m.11778G>A
        "rs199476118",  # LHON MT-ND1  m.3460G>A
        "rs199476104",  # LHON MT-ND6  m.14484T>C
        "rs267606617",  # MT-RNR1      m.1555A>G
        "rs267606619",  # MT-RNR1      m.1494C>T
        "rs267606618",  # MT-RNR1      m.1095T>C
    }
)


def _is_mitochondrial(locus: dict) -> bool:
    """A locus is mitochondrial when its HGNC gene symbol carries the ``MT-``
    prefix (all 37 mtDNA genes), or its label is an ``m.`` rCRS coordinate
    (covers non-coding / D-loop loci that have no ``MT-`` gene symbol)."""
    gene = locus.get("gene_symbol") or ""
    label = locus.get("label") or ""
    return gene.startswith("MT-") or bool(_MITO_LABEL_RE.match(label))


def _risk_genotype_panels() -> list[tuple[Path, dict]]:
    panels: list[tuple[Path, dict]] = []
    for path in sorted(_PANELS_DIR.glob("*.json")):
        try:
            panel = json.loads(path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(panel, dict) and panel.get("category") == "risk_genotype":
            panels.append((path, panel))
    return panels


def _mito_loci() -> list[tuple[Path, dict]]:
    out: list[tuple[Path, dict]] = []
    for path, panel in _risk_genotype_panels():
        for locus in panel.get("loci", []) or []:
            if isinstance(locus, dict) and _is_mitochondrial(locus):
                out.append((path, locus))
    return out


def test_mito_risk_loci_disable_strand_complement() -> None:
    """Every mitochondrial risk locus must set ``allow_strand_complement: false``.

    The default is ``True`` (correct for nuclear loci, dangerous for mtDNA), so a
    locus that simply omits the flag is a violation, not a pass.
    """
    violations = [
        f"{path.name}:{locus.get('rsid')} ({locus.get('label')})"
        for path, locus in _mito_loci()
        if locus.get("allow_strand_complement", True) is not False
    ]
    assert not violations, (
        "Mitochondrial risk loci must set `allow_strand_complement: false` — the "
        "rCRS is single-strand and a complemented mtDNA base is a different "
        "variant, so the reverse-strand fallback manufactures a false-positive "
        "risk call (see #30/#31/#87). Offenders:\n  " + "\n  ".join(violations)
    )


def test_guard_discovers_known_mito_loci() -> None:
    """The guard must not silently match nothing.

    If a refactor renames the gene/label fields, moves the panels, or breaks the
    mtDNA detector, the invariant test above would pass *vacuously*. Pin the known
    mitochondrial loci so the detector is itself covered.
    """
    discovered = {locus.get("rsid") for _, locus in _mito_loci()}
    missing = _KNOWN_MITO_RSIDS - discovered
    assert not missing, (
        "Known mitochondrial risk loci are no longer discovered by the guard — the "
        f"mtDNA detector or the panels changed: {sorted(missing)}. Fix the detector "
        "(or update _KNOWN_MITO_RSIDS) so the strand-complement invariant stays enforced."
    )
