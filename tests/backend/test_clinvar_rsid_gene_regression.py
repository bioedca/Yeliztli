"""Regression guard: the 11 wrong-gene ``expected_clinvar_rsids`` fixed in #1613.

Post-#1611, 11 rsIDs still sat in a panel gene's ``expected_clinvar_rsids`` while
their actual ClinVar GeneSymbol was a *different* gene — so a carrier of one could
be surfaced under the wrong disease gene/module. Each was verified against the
ClinVar Clinical Tables API (accessed 2026-07-07):

- 9 were **removed** — their true gene is not a P/LP panel gene (SLC19A2, KCNJ1,
  KCNQ1, LPIN1, SQSTM1, SMN2), or the row is not P/LP (rs746061888=NF1 Likely
  benign; rs137852989=ABCG8 Conflicting).
- 2 were **reassigned** to the true gene, which is an adjacent same-chromosome
  panel gene with a clean P/LP row (rs63749893 MSH2→MSH6 Pathogenic;
  rs137852988 ABCG5→ABCG8 Pathogenic/Likely pathogenic).

This locks the placement so the mismatches cannot silently return.
"""

from __future__ import annotations

import json
from pathlib import Path

_PANEL_DIR = Path(__file__).resolve().parents[2] / "backend" / "data" / "panels"
_PANEL_FILES = ("cancer_panel.json", "cardiovascular_panel.json", "carrier_panel.json")

# rsID -> (panel gene(s) it must NOT be listed under, the one gene it may appear
# under or None if it must be absent from all three panels). Genes are the true
# ClinVar GeneSymbol basis for each decision.
_FIXED: dict[str, dict[str, object]] = {
    "rs74315373": {"wrong": ("SDHB",), "correct": None},  # ClinVar: SLC19A2
    "rs104894251": {"wrong": ("MEN1",), "correct": None},  # ClinVar: KCNJ1
    "rs104894254": {"wrong": ("MEN1",), "correct": None},  # ClinVar: KCNJ1
    "rs104894255": {"wrong": ("MEN1",), "correct": None},  # ClinVar: KCNQ1 (cardiac)
    "rs746061888": {"wrong": ("RAD51C",), "correct": None},  # ClinVar: NF1, Likely benign
    "rs119480071": {"wrong": ("ABCG5",), "correct": None},  # ClinVar: LPIN1
    "rs137852989": {"wrong": ("ABCG5",), "correct": None},  # ClinVar: ABCG8, Conflicting
    "rs121909192": {"wrong": ("SMN1",), "correct": None},  # ClinVar: SMN2
    "rs104893941": {"wrong": ("SMN1",), "correct": None},  # ClinVar: SQSTM1
    "rs63749893": {"wrong": ("MSH2",), "correct": "MSH6"},  # ClinVar: MSH6, Pathogenic
    "rs137852988": {"wrong": ("ABCG5",), "correct": "ABCG8"},  # ClinVar: ABCG8, Path/LP
}


def _rsid_to_genes() -> dict[str, set[str]]:
    """Map every expected_clinvar_rsid to the panel gene symbol(s) listing it."""
    mapping: dict[str, set[str]] = {}
    for fname in _PANEL_FILES:
        data = json.loads((_PANEL_DIR / fname).read_text(encoding="utf-8"))
        for gene in data.get("genes", []):
            symbol = gene.get("gene_symbol") or gene.get("symbol") or gene.get("gene")
            for rsid in gene.get("expected_clinvar_rsids", []):
                mapping.setdefault(rsid, set()).add(symbol)
    return mapping


def test_wrong_gene_clinvar_rsids_stay_fixed() -> None:
    mapping = _rsid_to_genes()
    problems: list[str] = []
    for rsid, spec in _FIXED.items():
        genes = mapping.get(rsid, set())
        for wrong in spec["wrong"]:  # type: ignore[union-attr]
            if wrong in genes:
                problems.append(f"{rsid} is again listed under {wrong} (ClinVar says otherwise)")
        correct = spec["correct"]
        if correct is None:
            if genes:
                problems.append(
                    f"{rsid} should be absent from all panels but is under {sorted(genes)}"
                )
        else:
            if genes != {correct}:
                problems.append(
                    f"{rsid} should be listed only under {correct}, found {sorted(genes)}"
                )
    assert not problems, "expected_clinvar_rsid gene mismatches regressed (#1613):\n" + "\n".join(
        problems
    )
