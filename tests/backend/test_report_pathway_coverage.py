"""Static reports must not show a plain Standard badge for an incomplete pathway (#1651).

The interactive cards render coverage-qualified labels (``Tested Standard`` /
``Not Assessed``) via ``pathwayLevelDisplayLabel``; the bug was that the HTML/PDF
report and single-variant-card export path rendered the raw ``pathway_level``
instead, so an incomplete Standard pathway looked like a fully-assessed negative.
These lock the report-side ``pathway_level_display_label`` + both report finding
builders + the badge templates.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from backend.analysis.pathway_coverage import pathway_level_display_label
from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import findings, reference_metadata, samples
from backend.reports.generator import _load_findings, render_report_html
from backend.reports.variant_card import _load_single_finding


@pytest.mark.parametrize(
    "level, detail, expected",
    [
        ("Standard", {"missing_snps": ["rs1"], "called_snps": 2}, "Tested Standard"),
        ("Standard", {"missing_snps": ["rs1", "rs2"], "called_snps": 0}, "Not Assessed"),
        ("Standard", {"missing_snps": [], "called_snps": 3}, "Standard"),  # fully covered
        ("Standard", None, "Standard"),  # no coverage detail
        ("Elevated", {"missing_snps": ["rs1"], "called_snps": 0}, "Elevated"),  # non-Standard
        ("Moderate", {"missing_snps": ["rs1"], "called_snps": 1}, "Moderate"),
        (None, {"missing_snps": ["rs1"]}, None),
    ],
)
def test_pathway_level_display_label(level, detail, expected) -> None:
    assert pathway_level_display_label(level, detail) == expected


# ── report builders / end-to-end ────────────────────────────────────

_TESTED_STANDARD = {
    "module": "nutrigenomics",
    "category": "pathway_summary",
    "evidence_level": 1,
    "finding_text": "Caffeine Metabolism — no variants of concern among tested SNPs; "
    "2 tracked SNPs not assessed",
    "pathway": "Caffeine Metabolism",
    "pathway_level": "Standard",
    "detail_json": json.dumps({"called_snps": 3, "missing_snps": ["rs1", "rs2"]}),
}
_NOT_ASSESSED = {
    "module": "fitness",
    "category": "pathway_summary",
    "evidence_level": 1,
    "finding_text": "Power/Endurance — no tracked SNPs assessed; 2 tracked SNPs not assessed",
    "pathway": "Power/Endurance",
    "pathway_level": "Standard",
    "detail_json": json.dumps({"called_snps": 0, "missing_snps": ["rs1", "rs2"]}),
}


@pytest.fixture
def sample_with_incomplete_pathways(tmp_path: Path) -> tuple[Path, sa.Engine, sa.Engine]:
    data_dir = tmp_path / "data"
    (data_dir / "samples" / "sample_1").mkdir(parents=True)
    ref_engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
    reference_metadata.create_all(ref_engine)
    sample_engine = sa.create_engine(f"sqlite:///{data_dir / 'samples' / 'sample_1.db'}")
    create_sample_tables(sample_engine)
    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1, name="P", db_path="samples/sample_1.db", file_format="v5", file_hash="h"
            )
        )
    with sample_engine.begin() as conn:
        for f in (_TESTED_STANDARD, _NOT_ASSESSED):
            conn.execute(findings.insert().values(**f))
    return data_dir, ref_engine, sample_engine


def test_load_findings_carries_coverage_label(
    sample_with_incomplete_pathways: tuple[Path, sa.Engine, sa.Engine],
) -> None:
    _, _, sample_engine = sample_with_incomplete_pathways
    rows = {r["pathway"]: r for r in _load_findings(sample_engine, modules=None)}
    assert rows["Caffeine Metabolism"]["pathway_level_display"] == "Tested Standard"
    assert rows["Power/Endurance"]["pathway_level_display"] == "Not Assessed"


def test_variant_card_carries_coverage_label(
    sample_with_incomplete_pathways: tuple[Path, sa.Engine, sa.Engine],
) -> None:
    _, _, sample_engine = sample_with_incomplete_pathways
    with sample_engine.connect() as conn:
        fid = conn.execute(
            sa.select(findings.c.id).where(findings.c.pathway == "Caffeine Metabolism")
        ).scalar()
    entry = _load_single_finding(sample_engine, fid)
    assert entry["pathway_level_display"] == "Tested Standard"


def test_report_html_shows_coverage_badge_not_plain_standard(
    sample_with_incomplete_pathways: tuple[Path, sa.Engine, sa.Engine],
) -> None:
    data_dir, ref_engine, sample_engine = sample_with_incomplete_pathways
    settings = Settings(data_dir=data_dir, wal_mode=False)
    ref_engine.dispose()
    sample_engine.dispose()
    with (
        patch("backend.reports.generator.get_registry") as mock_reg,
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()
        from backend.db.connection import get_registry as real_get_reg

        mock_reg.return_value = real_get_reg()
        html = render_report_html(sample_id=1, modules=None)
        reset_registry()

    # Coverage-aware labels + their muted (non-green) badge classes render…
    assert "Tested Standard" in html
    assert "Not Assessed" in html
    assert "badge-tested-standard" in html
    assert "badge-not-assessed" in html
    # …and neither incomplete Standard pathway renders the clean green Standard badge.
    assert 'badge-standard">Standard<' not in html
