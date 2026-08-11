"""Tests for PDF report generator and HTML templates (P4-07 + P4-08).

Covers:
- T4-07: PDF report generates with all selected modules, disclaimers, PMIDs
- T4-08: Report respects module selection (excluded modules don't appear)
- T4-09: Findings sorted by evidence level (4-star first) within each section
- P4-08: Clinical typography, section headers, finding cards, EvidenceStars
         in print CSS, per-module disclaimer blocks, summary bar, TOC
- HTML rendering (no Playwright needed for these tests)
- Module disclaimer inclusion
- SVG embedding from disk
- API endpoint responses
"""

from __future__ import annotations

import json
import re
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.api.dependencies import sample_export_guard
from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    apoe_gate,
    findings,
    jobs,
    reference_metadata,
    samples,
)
from backend.disclaimers import (
    CARRIER_STATUS_DISCLAIMER_TEXT,
    CARRIER_STATUS_DISCLAIMER_TITLE,
)
from backend.reports.generator import (
    MAX_REPORT_FINDINGS,
    ReportTooLargeError,
    _group_findings_into_sections,
    _load_findings,
    render_report_html,
)
from backend.reports.module_disclaimers import MODULE_DISCLAIMERS, MODULE_DISPLAY_NAMES
from backend.services.sample_operation_lock import SAMPLE_EXPORT_JOB_TYPE

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()
    return data_dir


@pytest.fixture
def sample_with_findings(
    tmp_data_dir: Path,
) -> tuple[sa.Engine, sa.Engine, Path]:
    """Create reference + sample DBs seeded with diverse findings.

    Returns (ref_engine, sample_engine, sample_dir).
    """
    ref_path = tmp_data_dir / "reference.db"
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)

    sample_dir = tmp_data_dir / "samples" / "sample_1"
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)

    # Register sample
    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1,
                name="Test Patient",
                db_path="samples/sample_1.db",
                file_format="v5",
                file_hash="abc123",
            )
        )

    # Create SVG directory and a test SVG
    svgs_dir = sample_dir / "svgs"
    svgs_dir.mkdir(exist_ok=True)
    (svgs_dir / "1.svg").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="40">'
        '<rect width="200" height="40" fill="#0D9488"/></svg>\n',
        encoding="utf-8",
    )

    # Seed findings across multiple modules
    seed_findings = [
        {
            "module": "cancer",
            "category": "monogenic_variant",
            "evidence_level": 4,
            "gene_symbol": "BRCA1",
            "rsid": "rs80357906",
            "finding_text": "BRCA1 Pathogenic variant for Hereditary Breast Cancer",
            "clinvar_significance": "Pathogenic",
            "zygosity": "heterozygous",
            "pmid_citations": json.dumps(["12345678", "87654321"]),
            "svg_path": "svgs/1.svg",
            "detail_json": json.dumps({"syndromes": ["HBOC"]}),
        },
        {
            "module": "cancer",
            "category": "prs",
            "evidence_level": 2,
            "finding_text": "Breast Cancer PRS: 72nd percentile",
            "prs_score": 0.45,
            "prs_percentile": 72.0,
        },
        {
            "module": "pharmacogenomics",
            "category": "prescribing_alert",
            "evidence_level": 4,
            "gene_symbol": "CYP2C19",
            "diplotype": "*1/*2",
            "metabolizer_status": "Intermediate Metabolizer",
            "drug": "clopidogrel",
            "finding_text": "CYP2C19 *1/*2 — Intermediate Metabolizer for clopidogrel",
            "pmid_citations": json.dumps(["23698643"]),
        },
        {
            "module": "pharmacogenomics",
            "category": "prescribing_alert",
            "evidence_level": 3,
            "gene_symbol": "CYP2D6",
            "diplotype": "*1/*4",
            "metabolizer_status": "Intermediate Metabolizer",
            "drug": "codeine",
            "finding_text": "CYP2D6 *1/*4 — Intermediate Metabolizer for codeine",
        },
        {
            "module": "nutrigenomics",
            "category": "pathway_summary",
            "evidence_level": 2,
            "finding_text": "Folate Metabolism — Elevated consideration",
            "pathway": "Folate Metabolism",
            "pathway_level": "Elevated",
        },
        {
            "module": "nutrigenomics",
            "category": "pathway_summary",
            "evidence_level": 1,
            "finding_text": "Vitamin D — Standard",
            "pathway": "Vitamin D",
            "pathway_level": "Standard",
        },
        {
            "module": "carrier",
            "category": "monogenic_variant",
            "evidence_level": 3,
            "gene_symbol": "CFTR",
            "finding_text": "CFTR carrier — Cystic Fibrosis",
        },
        {
            "module": "ancestry",
            "category": "biogeographic",
            "evidence_level": 2,
            "finding_text": "82% European, 12% East Asian, 6% Other",
        },
        {
            "module": "ancestry",
            "category": "haplogroup",
            "evidence_level": 2,
            "finding_text": "mtDNA Haplogroup: H1a1",
            "haplogroup": "H1a1",
        },
        {
            "module": "traits",
            "category": "prs",
            "evidence_level": 2,
            "finding_text": "Educational attainment PRS: 65th percentile",
            "prs_percentile": 65.0,
        },
    ]
    with sample_engine.begin() as conn:
        for f in seed_findings:
            conn.execute(findings.insert().values(**f))

    return ref_engine, sample_engine, sample_dir


def _insert_gated_report_findings(sample_engine: sa.Engine) -> None:
    """Seed findings whose modules are hidden until their gates are acknowledged."""
    gated_findings = [
        {
            "module": "apoe",
            "category": "gated",
            "evidence_level": 4,
            "gene_symbol": "APOE",
            "finding_text": "Sensitive APOE report narrative",
        },
        {
            "module": "parkinsons",
            "category": "gated",
            "evidence_level": 4,
            "gene_symbol": "LRRK2",
            "finding_text": "Sensitive Parkinsons report narrative",
        },
        {
            "module": "sex_aneuploidy",
            "category": "gated",
            "evidence_level": 4,
            "finding_text": "Sensitive aneuploidy report narrative",
        },
    ]
    with sample_engine.begin() as conn:
        for finding in gated_findings:
            conn.execute(findings.insert().values(**finding))


def _insert_report_findings(
    sample_engine: sa.Engine,
    count: int,
    *,
    module: str = "rare_variants",
) -> None:
    """Bulk-seed synthetic reportable findings for size-boundary tests."""
    rows = [
        {
            "module": module,
            "category": "rare_variant",
            "evidence_level": 1,
            "finding_text": f"Synthetic report finding {index}",
        }
        for index in range(count)
    ]
    with sample_engine.begin() as conn:
        conn.execute(findings.insert(), rows)


def _insert_active_annotation_job(
    ref_engine: sa.Engine,
    sample_id: int = 1,
    *,
    status: str = "running",
) -> None:
    """Seed a running annotation job for export interlock tests."""
    with ref_engine.begin() as conn:
        conn.execute(
            jobs.insert().values(
                job_id=f"annotation-{sample_id}",
                sample_id=sample_id,
                job_type="annotation",
                status=status,
            )
        )


def _acknowledge_gate(sample_engine: sa.Engine, gate_table: sa.Table) -> None:
    with sample_engine.begin() as conn:
        conn.execute(gate_table.insert().values(id=1, acknowledged=True))


@pytest.fixture
def report_client(
    tmp_data_dir: Path,
    sample_with_findings: tuple[sa.Engine, sa.Engine, Path],
) -> Generator[TestClient, None, None]:
    """FastAPI test client with sample + findings pre-seeded."""
    ref_engine, sample_engine, _ = sample_with_findings
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_engine.dispose()
    sample_engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()
        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            yield tc
        reset_registry()


# ── Unit tests: findings loading ──────────────────────────────────


# The exact narrative persisted by ROH before the #2177 evaluability gate.
_LEGACY_ROH_TEXT = (
    "No long runs of homozygosity were detected (FROH ≈ 0). This is the typical result."
)


class TestLoadFindings:
    """Test _load_findings helper."""

    def test_loads_all_findings(self, tmp_data_dir: Path, sample_with_findings: tuple) -> None:
        _, sample_engine, _ = sample_with_findings
        results = _load_findings(sample_engine, modules=None)
        assert len(results) == 10

    def test_filters_by_module(self, tmp_data_dir: Path, sample_with_findings: tuple) -> None:
        _, sample_engine, _ = sample_with_findings
        results = _load_findings(sample_engine, modules=["cancer"])
        assert len(results) == 2
        assert all(r["module"] == "cancer" for r in results)

    def test_filters_multiple_modules(
        self, tmp_data_dir: Path, sample_with_findings: tuple
    ) -> None:
        _, sample_engine, _ = sample_with_findings
        results = _load_findings(sample_engine, modules=["cancer", "pharmacogenomics"])
        assert len(results) == 4
        modules = {r["module"] for r in results}
        assert modules == {"cancer", "pharmacogenomics"}

    def _seed_eligible_markers(self, sample_engine: sa.Engine) -> None:
        """A region a run could occupy — the legacy rule re-derives from markers."""
        from tests.backend._roh_fixtures import seed_segment_eligible_markers

        seed_segment_eligible_markers(sample_engine)

    def _insert_roh(self, sample_engine: sa.Engine, *, text: str, snps_used: int) -> None:
        from backend.db.tables import findings as findings_table

        with sample_engine.begin() as conn:
            conn.execute(
                sa.insert(findings_table),
                [
                    {
                        "module": "roh",
                        "category": "autozygosity",
                        "evidence_level": 1,
                        "finding_text": text,
                        "detail_json": json.dumps(
                            {"froh": 0.0, "n_segments": 0, "autosomal_snps_used": snps_used}
                        ),
                    }
                ],
            )

    def test_legacy_roh_typical_text_is_corrected_for_reports(
        self, sample_with_findings: tuple
    ) -> None:
        # #2177 — generated reports render the persisted finding_text directly,
        # so a pre-gate row would otherwise carry the "typical" negative into a
        # PDF for a sample whose markers cannot produce a segment at all.
        _, sample_engine, _ = sample_with_findings
        self._insert_roh(
            sample_engine,
            text=_LEGACY_ROH_TEXT,
            snps_used=30,
        )

        roh = [r for r in _load_findings(sample_engine, modules=["roh"])]
        assert len(roh) == 1
        assert "typical result" not in roh[0]["finding_text"].lower()
        assert "not assessed" in roh[0]["finding_text"].lower()

    def test_evaluable_roh_text_reaches_reports_unchanged(
        self, sample_with_findings: tuple
    ) -> None:
        # Counterpart control: a densely covered ROH negative must still read as
        # a genuine negative in the report.
        from tests.backend._roh_fixtures import ELIGIBLE_MARKER_COUNT

        _, sample_engine, _ = sample_with_findings
        self._seed_eligible_markers(sample_engine)
        stored = _LEGACY_ROH_TEXT
        self._insert_roh(sample_engine, text=stored, snps_used=ELIGIBLE_MARKER_COUNT)

        roh = [r for r in _load_findings(sample_engine, modules=["roh"])]
        assert len(roh) == 1
        assert roh[0]["finding_text"] == stored

    def test_allows_a_selection_at_the_report_limit(self, sample_with_findings: tuple) -> None:
        _, sample_engine, _ = sample_with_findings
        _insert_report_findings(sample_engine, MAX_REPORT_FINDINGS)

        results = _load_findings(sample_engine, modules=["rare_variants"])

        assert len(results) == MAX_REPORT_FINDINGS
        assert all(result["module"] == "rare_variants" for result in results)

    def test_rejects_a_selection_over_the_report_limit(self, sample_with_findings: tuple) -> None:
        _, sample_engine, _ = sample_with_findings
        _insert_report_findings(sample_engine, MAX_REPORT_FINDINGS + 1)
        finding_selects: list[str] = []

        def capture_finding_select(
            _conn,
            _cursor,
            statement: str,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            if "FROM findings" in statement:
                finding_selects.append(statement)

        sa.event.listen(sample_engine, "before_cursor_execute", capture_finding_select)
        try:
            with pytest.raises(ReportTooLargeError, match="maximum of 1,000 findings"):
                _load_findings(sample_engine, modules=["rare_variants"])
        finally:
            sa.event.remove(sample_engine, "before_cursor_execute", capture_finding_select)

        assert len(finding_selects) == 1
        assert "ORDER BY" not in finding_selects[0].upper()

    def test_rejects_an_unfiltered_selection_over_the_report_limit(
        self, sample_with_findings: tuple
    ) -> None:
        """The ``modules is None`` branch must be bounded too (#1990).

        Every other boundary test passes an explicit module list, so they all
        exercise only the filtered branch.  The default report applies no module
        filter at all, and that unfiltered default is precisely the runaway this
        guard exists for -- 311,467 findings in the issue's reproduction.

        Asserting only that ``ReportTooLargeError`` is raised would *not*
        discriminate this branch: the ordered ``LIMIT + 1`` recheck further down
        raises the same error, so the test would still pass with the unordered
        preflight disabled.  Pin the preflight itself, exactly as the filtered
        case does -- the point of this guard is to refuse before SQLite sorts
        every matching row.
        """
        _, sample_engine, _ = sample_with_findings
        _insert_report_findings(sample_engine, MAX_REPORT_FINDINGS + 1)
        finding_selects: list[str] = []

        def capture_finding_select(
            _conn,
            _cursor,
            statement: str,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            if "FROM findings" in statement:
                finding_selects.append(statement)

        sa.event.listen(sample_engine, "before_cursor_execute", capture_finding_select)
        try:
            with pytest.raises(ReportTooLargeError, match="maximum of 1,000 findings"):
                _load_findings(sample_engine, modules=None)
        finally:
            sa.event.remove(sample_engine, "before_cursor_execute", capture_finding_select)

        assert len(finding_selects) == 1
        assert "ORDER BY" not in finding_selects[0].upper()

    def test_allows_an_unfiltered_selection_at_the_report_limit(
        self, sample_with_findings: tuple
    ) -> None:
        """Counterpart control for the unfiltered branch.

        Without it, a guard that refused *every* unfiltered selection would
        still satisfy the rejection test above.
        """
        _, sample_engine, _ = sample_with_findings
        with sample_engine.begin() as conn:
            conn.execute(sa.delete(findings))
        _insert_report_findings(sample_engine, MAX_REPORT_FINDINGS)

        results = _load_findings(sample_engine, modules=None)

        assert len(results) == MAX_REPORT_FINDINGS

    def test_withholds_unqualified_local_ancestry(self, sample_with_findings: tuple) -> None:
        _, sample_engine, _ = sample_with_findings
        with sample_engine.begin() as conn:
            conn.execute(
                findings.insert().values(
                    module="ancestry",
                    category="local_ancestry",
                    evidence_level=4,
                    finding_text="Unqualified legacy chromosome painting",
                )
            )

        all_results = _load_findings(sample_engine, modules=None)
        ancestry_results = _load_findings(sample_engine, modules=["ancestry"])

        assert all_results
        assert ancestry_results
        assert all(result["category"] != "local_ancestry" for result in all_results)
        assert all(result["category"] != "local_ancestry" for result in ancestry_results)

    def test_withholds_retained_custom_tamoxifen_alert(self, sample_with_findings: tuple) -> None:
        """#2019: PDF/preview inputs cannot re-render a held alert row."""
        _, sample_engine, _ = sample_with_findings
        with sample_engine.begin() as conn:
            conn.execute(
                findings.insert().values(
                    module="medication_review",
                    category="prescribing_alert",
                    evidence_level=4,
                    gene_symbol=" CYP2D6 ",
                    drug="\ttamoxifen\n",
                    finding_text="Custom retained tamoxifen clinical advice.",
                )
            )
            conn.execute(
                findings.insert().values(
                    module="pharmacogenomics",
                    category="prescribing_alert",
                    evidence_level=4,
                    gene_symbol="CYP2C19",
                    drug="clopidogrel",
                    finding_text="Nested payload shell must not reach the report.",
                    detail_json=json.dumps(
                        {
                            "legacy": {
                                " Gene ": "CYP2D6",
                                "DRUG": "tamoxifen",
                                "recommendation": "Nested tamoxifen guidance must not render.",
                            }
                        }
                    ),
                )
            )

        results = _load_findings(sample_engine, modules=None)

        assert "CYP2D6 *1/*4 — Intermediate Metabolizer for codeine" in {
            result["finding_text"] for result in results
        }
        assert "Custom retained tamoxifen clinical advice." not in {
            result["finding_text"] for result in results
        }
        assert "Nested payload shell must not reach the report." not in {
            result["finding_text"] for result in results
        }

    def test_withholds_report_when_safe_rows_assemble_a_held_pair(
        self, sample_with_findings: tuple
    ) -> None:
        _, sample_engine, _ = sample_with_findings
        with sample_engine.begin() as conn:
            conn.execute(
                findings.insert(),
                [
                    {
                        "module": "fitness",
                        "category": "pathway_summary",
                        "evidence_level": 4,
                        "gene_symbol": "CYP2D6",
                        "drug": None,
                        "finding_text": "Safe source row one",
                    },
                    {
                        "module": "fitness",
                        "category": "legacy_note",
                        "evidence_level": 4,
                        "gene_symbol": None,
                        "drug": "tamoxifen",
                        "finding_text": "Safe source row two",
                    },
                ],
            )

        assert _load_findings(sample_engine, modules=["fitness"]) == []

    def test_sorted_by_evidence_level_desc(
        self, tmp_data_dir: Path, sample_with_findings: tuple
    ) -> None:
        """T4-09: Findings sorted by evidence level (4-star first)."""
        _, sample_engine, _ = sample_with_findings
        results = _load_findings(sample_engine, modules=None)
        evidence_levels = [r["evidence_level"] or 0 for r in results]
        assert evidence_levels == sorted(evidence_levels, reverse=True)

    def test_pmid_citations_parsed(self, tmp_data_dir: Path, sample_with_findings: tuple) -> None:
        _, sample_engine, _ = sample_with_findings
        results = _load_findings(sample_engine, modules=["cancer"])
        brca = next(r for r in results if r["gene_symbol"] == "BRCA1")
        assert brca["pmid_citations"] == ["12345678", "87654321"]

    def test_withholds_unacknowledged_gated_modules(self, sample_with_findings: tuple) -> None:
        _, sample_engine, _ = sample_with_findings
        _insert_gated_report_findings(sample_engine)

        results = _load_findings(sample_engine, modules=None)
        modules = {r["module"] for r in results}

        assert "cancer" in modules
        assert {"apoe", "parkinsons", "sex_aneuploidy"}.isdisjoint(modules)
        assert all("Sensitive" not in r["finding_text"] for r in results)

    @pytest.mark.parametrize("module", ["apoe", "parkinsons", "sex_aneuploidy"])
    def test_withholds_explicit_unacknowledged_gated_module(
        self,
        sample_with_findings: tuple,
        module: str,
    ) -> None:
        _, sample_engine, _ = sample_with_findings
        _insert_gated_report_findings(sample_engine)

        assert _load_findings(sample_engine, modules=[module]) == []

    def test_releases_only_acknowledged_gated_module(self, sample_with_findings: tuple) -> None:
        _, sample_engine, _ = sample_with_findings
        _insert_gated_report_findings(sample_engine)
        _acknowledge_gate(sample_engine, apoe_gate)

        results = _load_findings(sample_engine, modules=["apoe", "parkinsons"])

        assert [r["module"] for r in results] == ["apoe"]
        assert results[0]["finding_text"] == "Sensitive APOE report narrative"


# ── Unit tests: fresh SVG rendering ──────────────────────────────


class TestFreshSvgRendering:
    def test_ignores_persisted_artifact_and_renders_from_gated_source(
        self, sample_with_findings: tuple
    ) -> None:
        _, sample_engine, sample_dir = sample_with_findings
        (sample_dir / "svgs" / "1.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg">'
            "<text>CYP2D6 tamoxifen dose guidance</text></svg>",
            encoding="utf-8",
        )

        rows = _load_findings(sample_engine, modules=["cancer"])
        sections = _group_findings_into_sections(rows, sample_dir, modules=["cancer"])
        finding = next(item for item in sections[0]["findings"] if item["id"] == 1)

        assert finding["svg_content"] is not None
        assert "<svg" in finding["svg_content"]
        assert "tamoxifen" not in finding["svg_content"].lower()
        assert "_svg_render_input" not in finding


# ── Unit tests: section grouping ──────────────────────────────────


class TestSectionGrouping:
    def test_groups_by_module(self, sample_with_findings: tuple) -> None:
        _, sample_engine, sample_dir = sample_with_findings
        rows = _load_findings(sample_engine, modules=None)
        sections = _group_findings_into_sections(rows, sample_dir, modules=None)
        module_names = [s["module"] for s in sections]
        # All seeded modules should appear
        assert "cancer" in module_names
        assert "pharmacogenomics" in module_names
        assert "nutrigenomics" in module_names
        assert "carrier" in module_names
        assert "ancestry" in module_names
        assert "traits" in module_names

    def test_module_selection_excludes_others(self, sample_with_findings: tuple) -> None:
        """T4-08: Excluded modules don't appear in output."""
        _, sample_engine, sample_dir = sample_with_findings
        rows = _load_findings(sample_engine, modules=["cancer", "ancestry"])
        sections = _group_findings_into_sections(rows, sample_dir, modules=["cancer", "ancestry"])
        module_names = [s["module"] for s in sections]
        assert "cancer" in module_names
        assert "ancestry" in module_names
        assert "pharmacogenomics" not in module_names
        assert "nutrigenomics" not in module_names

    def test_sections_follow_predefined_order(self, sample_with_findings: tuple) -> None:
        _, sample_engine, sample_dir = sample_with_findings
        rows = _load_findings(sample_engine, modules=None)
        sections = _group_findings_into_sections(rows, sample_dir, modules=None)
        module_names = [s["module"] for s in sections]
        # cancer should come before pharmacogenomics, which comes before nutrigenomics
        assert module_names.index("cancer") < module_names.index("pharmacogenomics")
        assert module_names.index("pharmacogenomics") < module_names.index("nutrigenomics")

    def test_display_names_set(self, sample_with_findings: tuple) -> None:
        _, sample_engine, sample_dir = sample_with_findings
        rows = _load_findings(sample_engine, modules=["cancer"])
        sections = _group_findings_into_sections(rows, sample_dir, modules=["cancer"])
        assert sections[0]["display_name"] == "Cancer Predisposition"

    def test_disclaimers_included(self, sample_with_findings: tuple) -> None:
        _, sample_engine, sample_dir = sample_with_findings
        rows = _load_findings(sample_engine, modules=["cancer"])
        sections = _group_findings_into_sections(rows, sample_dir, modules=["cancer"])
        assert sections[0]["disclaimer"] is not None
        assert "predisposition" in sections[0]["disclaimer"].lower()

    def test_carrier_module_uses_stored_key_display_name_and_disclaimer(
        self, sample_with_findings: tuple
    ) -> None:
        """Report grouping honors the finding module written by carrier_status.py."""
        _, sample_engine, sample_dir = sample_with_findings
        rows = _load_findings(sample_engine, modules=["carrier"])
        sections = _group_findings_into_sections(rows, sample_dir, modules=["carrier"])

        assert [section["module"] for section in sections] == ["carrier"]
        carrier_section = sections[0]
        assert carrier_section["display_name"] == "Carrier Status"
        assert carrier_section["disclaimer_title"] == CARRIER_STATUS_DISCLAIMER_TITLE
        assert carrier_section["disclaimer"] == CARRIER_STATUS_DISCLAIMER_TEXT

    def test_prs_module_display_names_do_not_fallback_title_case(
        self, sample_with_findings: tuple
    ) -> None:
        """Acronym and product labels stay aligned with report-builder labels."""
        _, sample_engine, sample_dir = sample_with_findings
        extra_findings = [
            {
                "module": "metabolic",
                "category": "prs",
                "evidence_level": 2,
                "finding_text": "Type 2 diabetes PRS",
            },
            {
                "module": "fh",
                "category": "prs",
                "evidence_level": 2,
                "finding_text": "LDL-C PRS",
            },
            {
                "module": "ebmd",
                "category": "prs",
                "evidence_level": 2,
                "finding_text": "Heel eBMD PRS",
            },
        ]
        with sample_engine.begin() as conn:
            for finding in extra_findings:
                conn.execute(findings.insert().values(**finding))

        rows = _load_findings(sample_engine, modules=["metabolic", "fh", "ebmd"])
        sections = _group_findings_into_sections(
            rows,
            sample_dir,
            modules=["metabolic", "fh", "ebmd"],
        )
        display_names = {section["module"]: section["display_name"] for section in sections}

        assert display_names == {
            "metabolic": "Metabolic (T2D & Obesity)",
            "fh": "Familial Hypercholesterolemia",
            "ebmd": "Bone Density (eBMD)",
        }
        assert "Fh" not in display_names.values()
        assert "Ebmd" not in display_names.values()

    def test_svg_content_embedded(self, sample_with_findings: tuple) -> None:
        _, sample_engine, sample_dir = sample_with_findings
        rows = _load_findings(sample_engine, modules=["cancer"])
        sections = _group_findings_into_sections(rows, sample_dir, modules=["cancer"])
        # BRCA1 finding has svg_path set
        brca = next(f for f in sections[0]["findings"] if f.get("gene_symbol") == "BRCA1")
        assert brca["svg_content"] is not None
        assert "<svg" in brca["svg_content"]

    def test_finding_count_correct(self, sample_with_findings: tuple) -> None:
        _, sample_engine, sample_dir = sample_with_findings
        rows = _load_findings(sample_engine, modules=None)
        sections = _group_findings_into_sections(rows, sample_dir, modules=None)
        cancer_section = next(s for s in sections if s["module"] == "cancer")
        assert cancer_section["finding_count"] == 2


# ── Shared HTML render helper ─────────────────────────────────────


def _render_html_helper(
    tmp_data_dir: Path,
    sample_with_findings: tuple,
    modules: list[str] | None = None,
) -> str:
    """Render report HTML with patched registry and settings."""
    ref_engine, sample_engine, _ = sample_with_findings
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
    ref_engine.dispose()
    sample_engine.dispose()

    with (
        patch("backend.reports.generator.get_registry") as mock_reg,
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()
        from backend.db.connection import get_registry as real_get_reg

        mock_reg.return_value = real_get_reg()
        html = render_report_html(sample_id=1, modules=modules)
        reset_registry()

    return html


# ── Unit tests: HTML rendering ────────────────────────────────────


class TestReportFooterVersion:
    """#2025: the rendered footer must carry the running application's version.

    Asserted against the HTML that ``render_report_html`` actually produces, not
    against ``generator.VERSION``. Comparing module constants would stay green if
    the renderer stopped passing ``version=``, passed the wrong template key, or
    the footer template stopped displaying it — and the footer is the whole
    user-visible surface of this defect.
    """

    def test_footer_shows_the_installed_app_version(
        self, tmp_data_dir: Path, sample_with_findings: tuple
    ) -> None:
        from backend.main import VERSION as API_VERSION
        from backend.version import app_version

        html = _render_html_helper(tmp_data_dir, sample_with_findings)

        assert f"Yeliztli v{app_version()}" in html
        # The report must not claim a version the running application lacks.
        assert f"Yeliztli v{API_VERSION}" in html
        # The specific regression: the stale literal must not reappear anywhere.
        assert "Yeliztli v0.1.0" not in html


class TestHtmlRendering:
    def test_render_report_html_all_modules(
        self,
        tmp_data_dir: Path,
        sample_with_findings: tuple,
    ) -> None:
        """T4-07: Report renders with all modules, disclaimers, PMIDs."""
        html = _render_html_helper(tmp_data_dir, sample_with_findings)

        assert "Yeliztli Genomic Report" in html
        assert "Test Patient" in html
        # Module headers present
        assert "Cancer Predisposition" in html
        assert "Pharmacogenomics" in html
        assert "Nutrigenomics" in html
        assert "Carrier Status" in html
        # Findings present
        assert "BRCA1" in html
        assert "CYP2C19" in html
        # PMIDs cited
        assert "12345678" in html
        assert "87654321" in html
        # Disclaimer present
        assert "predisposition is not diagnosis" in html.lower()

    def test_render_with_module_filter(
        self,
        tmp_data_dir: Path,
        sample_with_findings: tuple,
    ) -> None:
        """T4-08: Excluded modules don't appear."""
        html = _render_html_helper(tmp_data_dir, sample_with_findings, modules=["cancer"])

        assert "Cancer Predisposition" in html
        assert "BRCA1" in html
        # Excluded modules should not appear
        assert "CYP2C19" not in html
        assert "Folate Metabolism" not in html

    def test_render_report_html_hides_unacknowledged_gated_findings(
        self,
        tmp_data_dir: Path,
        sample_with_findings: tuple,
    ) -> None:
        _, sample_engine, _ = sample_with_findings
        _insert_gated_report_findings(sample_engine)

        html = _render_html_helper(tmp_data_dir, sample_with_findings)

        assert "BRCA1" in html
        assert "Sensitive APOE report narrative" not in html
        assert "Sensitive Parkinsons report narrative" not in html
        assert "Sensitive aneuploidy report narrative" not in html

    def test_render_report_html_hides_retained_custom_tamoxifen_alert(
        self,
        tmp_data_dir: Path,
        sample_with_findings: tuple,
    ) -> None:
        _, sample_engine, _ = sample_with_findings
        with sample_engine.begin() as conn:
            conn.execute(
                findings.insert().values(
                    module="medication_review",
                    category="prescribing_alert",
                    evidence_level=4,
                    gene_symbol=" CYP2D6 ",
                    drug="\ttamoxifen\n",
                    finding_text="Custom retained tamoxifen clinical advice.",
                )
            )

        html = _render_html_helper(tmp_data_dir, sample_with_findings)

        assert "CYP2D6 *1/*4 — Intermediate Metabolizer for codeine" in html
        assert "Custom retained tamoxifen clinical advice." not in html

    def test_render_report_html_hides_explicit_unacknowledged_gated_module(
        self,
        tmp_data_dir: Path,
        sample_with_findings: tuple,
    ) -> None:
        _, sample_engine, _ = sample_with_findings
        _insert_gated_report_findings(sample_engine)

        html = _render_html_helper(tmp_data_dir, sample_with_findings, modules=["apoe"])

        assert "Sensitive APOE report narrative" not in html
        assert "APOE Risk" not in html

    def test_evidence_stars_rendered(
        self,
        tmp_data_dir: Path,
        sample_with_findings: tuple,
    ) -> None:
        html = _render_html_helper(tmp_data_dir, sample_with_findings, modules=["cancer"])

        # Evidence stars are rendered as ★ characters
        assert "star-filled" in html
        assert "star-empty" in html


# ── P4-08 tests: clinical templates ──────────────────────────────


class TestClinicalTemplates:
    """P4-08: Report HTML templates with clinical typography,
    section headers, finding cards, EvidenceStars print CSS,
    per-module disclaimer blocks."""

    def test_clinical_typography_font_stack(
        self, tmp_data_dir: Path, sample_with_findings: tuple
    ) -> None:
        """Template uses clinical font stack."""
        html = _render_html_helper(tmp_data_dir, sample_with_findings)
        assert '"Inter"' in html
        assert "font-feature-settings" in html

    def test_summary_bar_rendered(self, tmp_data_dir: Path, sample_with_findings: tuple) -> None:
        """Template renders summary statistics bar."""
        html = _render_html_helper(tmp_data_dir, sample_with_findings)
        assert "summary-bar" in html
        assert "Modules" in html
        assert "Findings" in html
        assert "High Evidence" in html

    def test_high_evidence_count_value(
        self, tmp_data_dir: Path, sample_with_findings: tuple
    ) -> None:
        """Summary bar counts both three- and four-star findings."""
        html = _render_html_helper(tmp_data_dir, sample_with_findings)
        match = re.search(
            r'<span class="summary-stat-value">(\d+)</span>\s*'
            r'<span class="summary-stat-label">High Evidence</span>',
            html,
        )

        assert match is not None
        assert int(match.group(1)) == 4

    def test_table_of_contents(self, tmp_data_dir: Path, sample_with_findings: tuple) -> None:
        """Template renders table of contents when multiple modules."""
        html = _render_html_helper(tmp_data_dir, sample_with_findings)
        assert "toc" in html
        assert "Contents" in html
        assert "toc-entry" in html

    def test_numbered_section_headers(
        self, tmp_data_dir: Path, sample_with_findings: tuple
    ) -> None:
        """Module headers include section numbers."""
        html = _render_html_helper(tmp_data_dir, sample_with_findings)
        assert "module-number" in html
        assert "module-title" in html

    def test_finding_card_evidence_level_border(
        self, tmp_data_dir: Path, sample_with_findings: tuple
    ) -> None:
        """Finding cards have evidence-level color-coded left borders."""
        html = _render_html_helper(tmp_data_dir, sample_with_findings)
        assert "finding-card--level-4" in html
        assert "finding-card--level-2" in html

    def test_evidence_stars_with_label(
        self, tmp_data_dir: Path, sample_with_findings: tuple
    ) -> None:
        """Evidence stars include numeric label (n/4)."""
        html = _render_html_helper(tmp_data_dir, sample_with_findings)
        assert "star-label" in html
        assert "/4)" in html

    def test_evidence_stars_aria_label(
        self, tmp_data_dir: Path, sample_with_findings: tuple
    ) -> None:
        """Evidence stars have ARIA labels for accessibility."""
        html = _render_html_helper(tmp_data_dir, sample_with_findings)
        assert "aria-label" in html
        assert "out of 4 stars" in html

    def test_print_css_evidence_stars(
        self, tmp_data_dir: Path, sample_with_findings: tuple
    ) -> None:
        """Print CSS forces evidence star colors."""
        html = _render_html_helper(tmp_data_dir, sample_with_findings)
        assert "@media print" in html
        assert "print-color-adjust: exact" in html

    def test_module_disclaimer_icon(self, tmp_data_dir: Path, sample_with_findings: tuple) -> None:
        """Module disclaimers include warning icon."""
        html = _render_html_helper(tmp_data_dir, sample_with_findings)
        assert "disclaimer-icon" in html
        assert "disclaimer-body" in html

    def test_finding_count_badge_in_header(
        self, tmp_data_dir: Path, sample_with_findings: tuple
    ) -> None:
        """Module headers display finding count badge."""
        html = _render_html_helper(tmp_data_dir, sample_with_findings)
        assert "module-count" in html

    def test_global_disclaimer_styled(
        self, tmp_data_dir: Path, sample_with_findings: tuple
    ) -> None:
        """Global disclaimer uses distinct styling from module disclaimers."""
        html = _render_html_helper(tmp_data_dir, sample_with_findings)
        assert "global-disclaimer" in html
        assert "Important Disclaimer" in html

    def test_meta_labels_styled(self, tmp_data_dir: Path, sample_with_findings: tuple) -> None:
        """Finding metadata uses labeled styling."""
        html = _render_html_helper(tmp_data_dir, sample_with_findings)
        assert "meta-label" in html
        assert "meta-item" in html

    def test_macros_template_exists(self) -> None:
        """_macros.html template file exists."""
        from backend.reports.generator import TEMPLATES_DIR

        macros_path = TEMPLATES_DIR / "_macros.html"
        assert macros_path.exists(), "_macros.html template not found"

    def test_gene_symbol_italic(self, tmp_data_dir: Path, sample_with_findings: tuple) -> None:
        """Gene symbols rendered in italic (clinical convention)."""
        html = _render_html_helper(tmp_data_dir, sample_with_findings)
        assert "font-style: italic" in html


# ── Unit tests: module disclaimers ────────────────────────────────


class TestModuleDisclaimers:
    def test_all_major_modules_have_disclaimers(self) -> None:
        major_modules = [
            "cancer",
            "cardiovascular",
            "apoe",
            "pharmacogenomics",
            "nutrigenomics",
            "carrier",
            "ancestry",
            "traits",
        ]
        for mod in major_modules:
            assert mod in MODULE_DISCLAIMERS, f"Missing disclaimer for {mod}"
            assert "title" in MODULE_DISCLAIMERS[mod]
            assert "text" in MODULE_DISCLAIMERS[mod]
            assert len(MODULE_DISCLAIMERS[mod]["text"]) > 50

    def test_all_modules_have_display_names(self) -> None:
        for mod in MODULE_DISCLAIMERS:
            assert mod in MODULE_DISPLAY_NAMES, f"Missing display name for {mod}"


# ── Integration tests: API endpoints ──────────────────────────────


def test_annotation_idle_gate_fails_closed_when_job_state_is_unavailable() -> None:
    error = sa.exc.OperationalError(
        "SELECT jobs",
        {},
        RuntimeError("reference database unavailable"),
    )
    with patch("backend.api.dependencies.get_registry") as get_registry_mock:
        get_registry_mock.return_value.reference_engine.connect.side_effect = error

        with pytest.raises(HTTPException) as exc_info:
            with sample_export_guard(1, operation="test export"):
                raise AssertionError("unreachable")

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == (
        "Unable to verify annotation status for sample 1; the export was not started."
    )


class TestReportAPI:
    def test_preview_endpoint(self, report_client: TestClient) -> None:
        resp = report_client.post(
            "/api/reports/preview",
            json={"sample_id": 1},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        html = resp.text
        assert "Yeliztli Genomic Report" in html
        assert "Test Patient" in html

    def test_preview_with_modules(self, report_client: TestClient) -> None:
        resp = report_client.post(
            "/api/reports/preview",
            json={"sample_id": 1, "modules": ["cancer"]},
        )
        assert resp.status_code == 200
        html = resp.text
        assert "BRCA1" in html
        assert "CYP2C19" not in html

    def test_preview_custom_title(self, report_client: TestClient) -> None:
        resp = report_client.post(
            "/api/reports/preview",
            json={"sample_id": 1, "title": "My Custom Report"},
        )
        assert resp.status_code == 200
        assert "My Custom Report" in resp.text

    def test_preview_nonexistent_sample(self, report_client: TestClient) -> None:
        resp = report_client.post(
            "/api/reports/preview",
            json={"sample_id": 999},
        )
        assert resp.status_code == 404

    def test_generate_endpoint_returns_pdf(self, report_client: TestClient) -> None:
        """Test PDF generation endpoint with mocked Playwright."""
        fake_pdf = b"%PDF-1.4 fake pdf content"

        with patch(
            "backend.reports.generator._html_to_pdf",
            new_callable=AsyncMock,
            return_value=fake_pdf,
        ):
            resp = report_client.post(
                "/api/reports/generate",
                json={"sample_id": 1},
            )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert "attachment" in resp.headers["content-disposition"]
        assert resp.content == fake_pdf

    def test_generate_holds_export_lease_during_pdf_rendering(
        self,
        report_client: TestClient,
    ) -> None:
        from backend.db.connection import get_registry

        async def assert_lease_held(_html: str) -> bytes:
            with get_registry().reference_engine.connect() as conn:
                row = conn.execute(
                    sa.select(jobs.c.status).where(
                        jobs.c.sample_id == 1,
                        jobs.c.job_type == SAMPLE_EXPORT_JOB_TYPE,
                    )
                ).fetchone()
            assert row is not None
            assert row.status == "running"
            return b"%PDF-1.4 leased"

        with patch(
            "backend.reports.generator._html_to_pdf",
            new_callable=AsyncMock,
            side_effect=assert_lease_held,
        ):
            resp = report_client.post(
                "/api/reports/generate",
                json={"sample_id": 1},
            )

        assert resp.status_code == 200
        with get_registry().reference_engine.connect() as conn:
            row = conn.execute(
                sa.select(jobs.c.status).where(
                    jobs.c.sample_id == 1,
                    jobs.c.job_type == SAMPLE_EXPORT_JOB_TYPE,
                )
            ).fetchone()
        assert row is not None
        assert row.status == "complete"

    def test_generate_with_module_filter(self, report_client: TestClient) -> None:
        fake_pdf = b"%PDF-1.4 filtered report"

        with patch(
            "backend.reports.generator._html_to_pdf",
            new_callable=AsyncMock,
            return_value=fake_pdf,
        ):
            resp = report_client.post(
                "/api/reports/generate",
                json={"sample_id": 1, "modules": ["cancer", "pharmacogenomics"]},
            )

        assert resp.status_code == 200
        assert resp.content == fake_pdf

    def test_generate_rejects_oversized_selection_before_pdf_rendering(
        self,
        report_client: TestClient,
        sample_with_findings: tuple,
    ) -> None:
        _, sample_engine, _ = sample_with_findings
        _insert_report_findings(sample_engine, MAX_REPORT_FINDINGS + 1)

        with patch(
            "backend.reports.generator._html_to_pdf",
            new_callable=AsyncMock,
        ) as html_to_pdf:
            resp = report_client.post(
                "/api/reports/generate",
                json={"sample_id": 1, "modules": ["rare_variants"]},
            )

        assert resp.status_code == 413
        assert resp.json()["detail"] == (
            "Report selection exceeds the maximum of 1,000 findings; select fewer modules."
        )
        html_to_pdf.assert_not_awaited()

    def test_generate_rejects_active_annotation_before_pdf_rendering(
        self,
        report_client: TestClient,
        sample_with_findings: tuple,
    ) -> None:
        ref_engine, _, _ = sample_with_findings
        _insert_active_annotation_job(ref_engine)

        with patch(
            "backend.reports.generator._html_to_pdf",
            new_callable=AsyncMock,
        ) as html_to_pdf:
            resp = report_client.post(
                "/api/reports/generate",
                json={"sample_id": 1},
            )

        assert resp.status_code == 409
        assert resp.json()["detail"] == (
            "Annotation is in progress for sample 1; retry the export after it completes."
        )
        html_to_pdf.assert_not_awaited()

    def test_preview_rejects_oversized_selection_before_html_rendering(
        self,
        report_client: TestClient,
        sample_with_findings: tuple,
    ) -> None:
        _, sample_engine, _ = sample_with_findings
        _insert_report_findings(sample_engine, MAX_REPORT_FINDINGS + 1)

        resp = report_client.post(
            "/api/reports/preview",
            json={"sample_id": 1, "modules": ["rare_variants"]},
        )

        assert resp.status_code == 413
        assert resp.json()["detail"] == (
            "Report selection exceeds the maximum of 1,000 findings; select fewer modules."
        )

    def test_preview_rejects_active_annotation_before_html_rendering(
        self,
        report_client: TestClient,
        sample_with_findings: tuple,
    ) -> None:
        ref_engine, _, _ = sample_with_findings
        _insert_active_annotation_job(ref_engine, status="cancelling")

        with patch("backend.reports.generator.render_report_html") as render_html:
            resp = report_client.post(
                "/api/reports/preview",
                json={"sample_id": 1},
            )

        assert resp.status_code == 409
        assert resp.json()["detail"] == (
            "Annotation is in progress for sample 1; retry the export after it completes."
        )
        render_html.assert_not_called()

    def test_generate_nonexistent_sample(self, report_client: TestClient) -> None:
        resp = report_client.post(
            "/api/reports/generate",
            json={"sample_id": 999},
        )
        assert resp.status_code == 404

    def test_generate_playwright_not_installed(self, report_client: TestClient) -> None:
        """503 when Playwright browsers aren't available."""
        with patch(
            "backend.reports.generator._html_to_pdf",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Playwright is required"),
        ):
            resp = report_client.post(
                "/api/reports/generate",
                json={"sample_id": 1},
            )

        assert resp.status_code == 503
        assert "Playwright" in resp.json()["detail"]

    def test_empty_modules_list_rejected(self, report_client: TestClient) -> None:
        """Empty modules list should return 422 (use null for all)."""
        resp = report_client.post(
            "/api/reports/preview",
            json={"sample_id": 1, "modules": []},
        )
        assert resp.status_code == 422


# ── Legacy cross-module links in reports (#2021) ──────────────────────────


class TestLegacyCrossModuleLinksInReports:
    """A report is a durable export, so a dead link there outlives every screen.

    `_load_findings` renders persisted rows directly, so without read-time
    resolution a PDF generated for an existing sample would keep the retired
    celiac card and keep pointing FTO at Nutrigenomics — after the list and
    summary APIs had already stopped doing both.
    """

    def _insert_cross_module(
        self,
        sample_engine: sa.Engine,
        *,
        module: str,
        rsid: str,
        gene: str,
        note: str,
        text: str,
    ) -> None:
        with sample_engine.begin() as conn:
            conn.execute(
                findings.insert().values(
                    module=module,
                    category="cross_module",
                    evidence_level=3,
                    gene_symbol=gene,
                    rsid=rsid,
                    finding_text=text,
                    detail_json=json.dumps(
                        {
                            "source_module": module,
                            "target_module": "nutrigenomics",
                            "cross_module_note": note,
                        }
                    ),
                )
            )

    def test_retargeted_fto_link_is_corrected_for_reports(
        self, sample_with_findings: tuple
    ) -> None:
        _, sample_engine, _ = sample_with_findings
        note = (
            "FTO rs9939609 influences appetite regulation and macronutrient "
            "metabolism. See Nutrigenomics for dietary recommendations."
        )
        self._insert_cross_module(
            sample_engine,
            module="gene_health",
            rsid="rs9939609",
            gene="FTO",
            note=note,
            text=f"FTO FTO intron 1 (AT) — {note}",
        )

        rows = [r for r in _load_findings(sample_engine, modules=["gene_health"])]
        cross = [r for r in rows if r["category"] == "cross_module"]
        assert len(cross) == 1
        assert "See Nutrigenomics for dietary recommendations" not in cross[0]["finding_text"]
        assert "Metabolic module" in cross[0]["finding_text"]

    def test_retired_celiac_link_is_absent_from_reports(self, sample_with_findings: tuple) -> None:
        _, sample_engine, _ = sample_with_findings
        note = (
            "Celiac-related HLA-DQ2 finding may affect dietary considerations. "
            "See Nutrigenomics for gluten-related nutrient interactions."
        )
        self._insert_cross_module(
            sample_engine,
            module="allergy",
            rsid="rs2187668",
            gene="HLA-DQA1",
            note=note,
            text=f"HLA-DQ2.5 proxy (CT) — {note}",
        )

        rows = _load_findings(sample_engine, modules=["allergy"])
        assert [r for r in rows if r["category"] == "cross_module"] == []

    def test_unrelated_findings_still_reach_reports(self, sample_with_findings: tuple) -> None:
        """Control: normalization must not thin the report generally."""
        _, sample_engine, _ = sample_with_findings
        assert len(_load_findings(sample_engine, modules=None)) == 10
