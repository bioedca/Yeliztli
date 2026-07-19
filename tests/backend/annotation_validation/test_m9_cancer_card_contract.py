"""M9 — Cancer variant-card API contract (re-homed PR #316 regression guards).

These two guards were originally in ``tests/backend/test_cancer_analysis.py`` and
were removed when this branch's carriage work refactored that file. The M1–M8
live-path suite does not exercise the cancer module, so the guards are re-homed
here (see ``docs/test-suite-audit-and-ci-tiering.md`` Part 3 — the P0 blocker) so
the PR #316 fixes ship covered:

* ``store_cancer_findings`` must persist ``detail_json["genotype"]`` — the variant
  card renders its genotype line from that field (regression: it was omitted, so
  the line never rendered).
* ``_fetch_cancer_findings`` must be category-scoped to monogenic variants so
  cancer-PRS rows (``module == "cancer"``, ``category == "prs"``, no
  gene/rsid/significance) never leak into the variant grid as blank cards.

The ``panel`` and ``sample_with_cancer_variants`` fixtures are intentionally
duplicated from ``test_cancer_analysis.py`` so this guard is self-contained and
survives further refactors of that file. ``sample_engine`` is inherited from
``tests/backend/conftest.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from backend.analysis.cancer import (
    CancerPanel,
    extract_cancer_variants,
    load_cancer_panel,
    store_cancer_findings,
)
from backend.db.tables import annotated_variants, findings

PANEL_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "backend"
    / "data"
    / "panels"
    / "cancer_panel.json"
)


@pytest.fixture()
def panel() -> CancerPanel:
    """Load the curated cancer panel from the real JSON file."""
    return load_cancer_panel(PANEL_PATH)


@pytest.fixture()
def sample_with_cancer_variants(sample_engine: sa.Engine) -> sa.Engine:
    """Sample engine with annotated variants including cancer panel P/LP hits."""
    variants = [
        {
            "rsid": "rs80357906",
            "chrom": "17",
            "pos": 41209080,
            "genotype": "CT",
            "zygosity": "het",
            "gene_symbol": "BRCA1",
            "clinvar_significance": "Pathogenic",
            "clinvar_review_stars": 3,
            "clinvar_accession": "VCV000017677",
            "clinvar_conditions": "Hereditary breast and ovarian cancer syndrome",
            "annotation_coverage": 2,
        },
        {
            "rsid": "rs28934578",
            "chrom": "17",
            "pos": 7577538,
            "genotype": "CG",
            "zygosity": "het",
            "gene_symbol": "TP53",
            "clinvar_significance": "Likely pathogenic",
            "clinvar_review_stars": 2,
            "clinvar_accession": "VCV000012347",
            "clinvar_conditions": "Li-Fraumeni syndrome",
            "annotation_coverage": 2,
        },
        {
            "rsid": "rs63751710",
            "chrom": "3",
            "pos": 37053568,
            "genotype": "AG",
            "zygosity": "het",
            "gene_symbol": "MLH1",
            "clinvar_significance": "Pathogenic",
            "clinvar_review_stars": 1,
            "clinvar_accession": "VCV000036555",
            "clinvar_conditions": "Lynch syndrome",
            "annotation_coverage": 2,
        },
        {
            "rsid": "rs587779317",
            "chrom": "11",
            "pos": 108098576,
            "genotype": "CT",
            "zygosity": "het",
            "gene_symbol": "ATM",
            "clinvar_significance": "Likely pathogenic",
            "clinvar_review_stars": 1,
            "clinvar_accession": "VCV000127345",
            "clinvar_conditions": "Ataxia-telangiectasia",
            "annotation_coverage": 2,
        },
        {
            "rsid": "rs80359550",
            "chrom": "13",
            "pos": 32914438,
            "genotype": "AG",
            "zygosity": "het",
            "gene_symbol": "BRCA2",
            "clinvar_significance": "Pathogenic",
            "clinvar_review_stars": 0,
            "clinvar_accession": "VCV000009325",
            "clinvar_conditions": "Hereditary breast and ovarian cancer syndrome",
            "annotation_coverage": 2,
        },
        # Benign / non-panel / VUS — must NOT surface as monogenic findings.
        {
            "rsid": "rs1801155",
            "chrom": "5",
            "pos": 112175770,
            "genotype": "TG",
            "zygosity": "het",
            "gene_symbol": "APC",
            "clinvar_significance": "Benign",
            "clinvar_review_stars": 2,
            "clinvar_accession": "VCV000012999",
            "clinvar_conditions": "Familial adenomatous polyposis",
            "annotation_coverage": 2,
        },
        {
            "rsid": "rs113993960",
            "chrom": "7",
            "pos": 117199645,
            "genotype": "CT",
            "zygosity": "het",
            "gene_symbol": "CFTR",
            "clinvar_significance": "Pathogenic",
            "clinvar_review_stars": 3,
            "clinvar_accession": "VCV000007105",
            "clinvar_conditions": "Cystic fibrosis",
            "annotation_coverage": 2,
        },
        {
            "rsid": "rs999888",
            "chrom": "17",
            "pos": 43092000,
            "genotype": "AG",
            "zygosity": "het",
            "gene_symbol": "BRCA1",
            "clinvar_significance": "Uncertain_significance",
            "clinvar_review_stars": 1,
            "clinvar_accession": "VCV000099999",
            "clinvar_conditions": "not specified",
            "annotation_coverage": 2,
        },
    ]
    with sample_engine.begin() as conn:
        conn.execute(sa.insert(annotated_variants), variants)
    return sample_engine


class TestCancerCardContract:
    """PR #316 regression guards for the cancer variant-card API."""

    def test_detail_json_has_genotype(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        # The variant card renders genotype from detail_json["genotype"];
        # store_cancer_findings must persist it (regression: it was omitted,
        # so the card's genotype line never rendered).
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        store_cancer_findings(result, sample_with_cancer_variants)

        with sample_with_cancer_variants.connect() as conn:
            row = conn.execute(
                sa.select(findings).where(findings.c.rsid == "rs80357906")
            ).fetchone()
        detail = json.loads(row.detail_json)
        assert detail["genotype"] == "CT"

    def test_prs_findings_excluded_from_variants(
        self, panel: CancerPanel, sample_with_cancer_variants: sa.Engine
    ) -> None:
        """``_fetch_cancer_findings`` must return only monogenic variant findings.

        PRS findings share ``module == "cancer"`` (category ``"prs"``) but have no
        ``gene_symbol`` / ``rsid`` / ``clinvar_significance``. If they leak into the
        variants endpoint they render as blank cards in the monogenic grid (the
        reported bug). The fetch helper is category-scoped to prevent that.
        """
        from backend.api.routes.cancer import _fetch_cancer_findings

        # Store the monogenic findings, then add a PRS finding the way
        # store_cancer_prs_findings would (module=cancer, category=prs,
        # no gene/rsid/significance columns).
        result = extract_cancer_variants(panel, sample_with_cancer_variants)
        store_cancer_findings(result, sample_with_cancer_variants)
        with sample_with_cancer_variants.begin() as conn:
            conn.execute(
                sa.insert(findings),
                [
                    {
                        "module": "cancer",
                        "category": "prs",
                        "evidence_level": 1,
                        "finding_text": "Breast cancer PRS: 80th percentile",
                        "prs_percentile": 80.0,
                        "detail_json": json.dumps({"trait": "breast", "name": "Breast"}),
                    }
                ],
            )

        rows = _fetch_cancer_findings(sample_with_cancer_variants)

        # Only the 5 monogenic P/LP variants — the PRS row is filtered out.
        assert len(rows) == 5
        # Every returned card has the content the UI needs (non-blank): the
        # leaked PRS rows would have empty gene_symbol/rsid/significance.
        assert all(r["gene_symbol"] and r["rsid"] and r["clinvar_significance"] for r in rows)
