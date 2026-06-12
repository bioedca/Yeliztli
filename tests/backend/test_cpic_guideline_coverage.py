"""CPIC prescribing-alert coverage guard (issue #23).

``generate_prescribing_alerts`` builds alerts via an exact ``gene + phenotype``
lookup against ``cpic_guidelines``. A callable phenotype with no matching
guideline row produces no alert and no signal — the actionable drug-gene pair
is silently dropped, and a missing row is indistinguishable from "intentionally
no recommendation".

This module turns that silent class into:

1. A **coverage audit** over the shipped bundled CPIC data and seed fixture:
   for every ``(gene, drug)`` in ``cpic_guidelines``, every phenotype callable
   for that gene (from ``cpic_diplotypes``) must have a guideline row, or be
   listed in the explicit, reviewed ``KNOWN_NO_GUIDANCE`` allowlist. Silent
   omissions become a failing test.
2. **Telemetry** assertions: a non-``Insufficient``, callable phenotype with no
   guideline row emits a ``pgx_phenotype_no_guideline_row`` warning when the
   gene is otherwise covered (a likely missing row), and stays quiet for a gene
   with no guidelines at all.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import sqlalchemy as sa
from structlog.testing import capture_logs

from backend.analysis.pharmacogenomics import (
    CallConfidence,
    StarAlleleResult,
    _fetch_guideline_phenotypes_for_gene,
    generate_prescribing_alerts,
)
from backend.db.tables import cpic_guidelines, reference_metadata

_CPIC_DIR = Path(__file__).resolve().parents[2] / "backend" / "data" / "cpic"
_DIPLOTYPES_CSV = _CPIC_DIR / "cpic_diplotypes.csv"
_GUIDELINES_CSV = _CPIC_DIR / "cpic_guidelines.csv"
_SEED_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "seed_csvs"
_SEED_DIPLOTYPES_CSV = _SEED_DIR / "cpic_diplotypes_seed.csv"
_SEED_GUIDELINES_CSV = _SEED_DIR / "cpic_guidelines_seed.csv"
_GUIDELINE_COVERAGE_SOURCES = (
    ("bundled", _DIPLOTYPES_CSV, _GUIDELINES_CSV),
    ("seed", _SEED_DIPLOTYPES_CSV, _SEED_GUIDELINES_CSV),
)

# Explicit, reviewed (gene, drug, phenotype) tuples where CPIC genuinely makes
# no special recommendation AND we deliberately ship no row. Keep EMPTY unless a
# real no-guidance case is confirmed against the published CPIC guideline; an
# entry here is a documented decision, not a convenient way to silence a gap.
KNOWN_NO_GUIDANCE: set[tuple[str, str, str]] = set()


def _callable_phenotypes_by_gene(diplotypes_csv: Path) -> dict[str, set[str]]:
    """gene -> set of phenotypes reachable from any diplotype CSV."""
    by_gene: dict[str, set[str]] = defaultdict(set)
    with open(diplotypes_csv, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["phenotype"]:
                by_gene[row["gene"]].add(row["phenotype"])
    return by_gene


def _guideline_phenotypes_by_gene_drug(
    guidelines_csv: Path = _GUIDELINES_CSV,
) -> dict[tuple[str, str], set[str]]:
    """(gene, drug) -> set of phenotypes with a guideline row."""
    by_pair: dict[tuple[str, str], set[str]] = defaultdict(set)
    with open(guidelines_csv, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_pair[(row["gene"], row["drug"])].add(row["phenotype"])
    return by_pair


class TestGuidelineCoverage:
    """CPIC guideline tables cover every callable phenotype."""

    def test_every_callable_phenotype_has_a_guideline_row(self) -> None:
        gaps: list[str] = []
        for source_name, diplotypes_csv, guidelines_csv in _GUIDELINE_COVERAGE_SOURCES:
            callable_by_gene = _callable_phenotypes_by_gene(diplotypes_csv)
            guideline_by_pair = _guideline_phenotypes_by_gene_drug(guidelines_csv)

            for (gene, drug), covered in sorted(guideline_by_pair.items()):
                for phenotype in sorted(callable_by_gene.get(gene, set())):
                    if phenotype in covered:
                        continue
                    if (gene, drug, phenotype) in KNOWN_NO_GUIDANCE:
                        continue
                    gaps.append(f"{source_name}: {gene} / {drug} / {phenotype}")

        assert not gaps, (
            "Callable phenotypes with no cpic_guidelines row and no "
            "KNOWN_NO_GUIDANCE marker (silent prescribing-alert gaps):\n  " + "\n  ".join(gaps)
        )

    def test_cyp2c19_voriconazole_intermediate_covered(self) -> None:
        """Regression for the IM gap surfaced by issue #23.

        CPIC recommends standard dosing with therapeutic drug monitoring for
        CYP2C19 intermediate metabolizers on voriconazole.
        """
        for source_name, _, guidelines_csv in _GUIDELINE_COVERAGE_SOURCES:
            covered = _guideline_phenotypes_by_gene_drug(guidelines_csv)[
                ("CYP2C19", "voriconazole")
            ]
            assert "Intermediate Metabolizer" in covered, source_name

    def test_cyp2d6_ondansetron_intermediate_covered(self) -> None:
        """Regression for the IM gap surfaced by issue #23.

        CPIC's ondansetron recommendation is identical for normal and
        intermediate metabolizers (standard dosing); only ultrarapid differs.
        """
        for source_name, _, guidelines_csv in _GUIDELINE_COVERAGE_SOURCES:
            covered = _guideline_phenotypes_by_gene_drug(guidelines_csv)[("CYP2D6", "ondansetron")]
            assert "Intermediate Metabolizer" in covered, source_name


def _reference_engine_with_guidelines(rows: list[dict]) -> sa.Engine:
    engine = sa.create_engine("sqlite://")
    reference_metadata.create_all(engine)
    if rows:
        with engine.begin() as conn:
            conn.execute(cpic_guidelines.insert(), rows)
    return engine


def _result(gene: str, phenotype: str) -> StarAlleleResult:
    return StarAlleleResult(
        gene=gene,
        allele1="*1",
        allele2="*1",
        diplotype="*1/*1",
        phenotype=phenotype,
        call_confidence=CallConfidence.COMPLETE,
        confidence_note="All defining positions assessed.",
    )


class TestCoverageGapTelemetry:
    """A missing guideline row for a covered gene is a visible warning."""

    def test_warns_when_gene_covered_but_phenotype_row_missing(self) -> None:
        # GENEX has a row for Normal Metabolizer but not Poor Metabolizer.
        engine = _reference_engine_with_guidelines(
            [
                {
                    "gene": "GENEX",
                    "drug": "drugx",
                    "phenotype": "Normal Metabolizer",
                    "recommendation": "Use label-recommended dosing.",
                    "classification": "A",
                    "guideline_url": "https://example.org/genex",
                }
            ]
        )
        with capture_logs() as logs:
            alerts = generate_prescribing_alerts([_result("GENEX", "Poor Metabolizer")], engine)

        assert alerts == []
        warnings = [
            entry for entry in logs if entry.get("event") == "pgx_phenotype_no_guideline_row"
        ]
        assert len(warnings) == 1
        assert warnings[0]["log_level"] == "warning"
        assert warnings[0]["gene"] == "GENEX"
        assert warnings[0]["phenotype"] == "Poor Metabolizer"
        assert warnings[0]["covered_phenotypes"] == ["Normal Metabolizer"]

    def test_no_warning_when_gene_has_no_guidelines(self) -> None:
        engine = _reference_engine_with_guidelines([])
        with capture_logs() as logs:
            alerts = generate_prescribing_alerts([_result("GENEX", "Poor Metabolizer")], engine)

        assert alerts == []
        assert not [
            entry for entry in logs if entry.get("event") == "pgx_phenotype_no_guideline_row"
        ]

    def test_no_warning_when_phenotype_is_covered(self) -> None:
        engine = _reference_engine_with_guidelines(
            [
                {
                    "gene": "GENEX",
                    "drug": "drugx",
                    "phenotype": "Poor Metabolizer",
                    "recommendation": "Avoid drugx.",
                    "classification": "A",
                    "guideline_url": "https://example.org/genex",
                }
            ]
        )
        with capture_logs() as logs:
            alerts = generate_prescribing_alerts([_result("GENEX", "Poor Metabolizer")], engine)

        assert len(alerts) == 1
        assert not [
            entry for entry in logs if entry.get("event") == "pgx_phenotype_no_guideline_row"
        ]

    def test_fetch_guideline_phenotypes_for_gene(self) -> None:
        engine = _reference_engine_with_guidelines(
            [
                {
                    "gene": "GENEX",
                    "drug": "drugx",
                    "phenotype": "Normal Metabolizer",
                    "recommendation": "Use label-recommended dosing.",
                    "classification": "A",
                    "guideline_url": "https://example.org/genex",
                },
                {
                    "gene": "GENEX",
                    "drug": "drugy",
                    "phenotype": "Poor Metabolizer",
                    "recommendation": "Avoid drugy.",
                    "classification": "A",
                    "guideline_url": "https://example.org/genex",
                },
            ]
        )
        assert _fetch_guideline_phenotypes_for_gene("GENEX", engine) == {
            "Normal Metabolizer",
            "Poor Metabolizer",
        }
        assert _fetch_guideline_phenotypes_for_gene("MISSING", engine) == set()
