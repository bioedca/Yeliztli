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
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import findings, reference_metadata, samples
from backend.disclaimers import (
    CARRIER_STATUS_DISCLAIMER_TEXT,
    CARRIER_STATUS_DISCLAIMER_TITLE,
)
from backend.reports.generator import (
    _group_findings_into_sections,
    _load_findings,
    _read_svg_content,
    render_report_html,
)
from backend.reports.module_disclaimers import MODULE_DISCLAIMERS, MODULE_DISPLAY_NAMES

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


# ── Unit tests: SVG reading ──────────────────────────────────────


class TestSvgReading:
    def test_reads_svg_content(self, sample_with_findings: tuple) -> None:
        _, _, sample_dir = sample_with_findings
        content = _read_svg_content(sample_dir, "svgs/1.svg")
        assert content is not None
        assert "<svg" in content
        # XML declaration should be stripped for inline embedding
        assert "<?xml" not in content

    def test_returns_none_for_missing_svg(self, tmp_path: Path) -> None:
        assert _read_svg_content(tmp_path, "nonexistent.svg") is None

    def test_returns_none_for_empty_path(self, tmp_path: Path) -> None:
        assert _read_svg_content(tmp_path, None) is None
        assert _read_svg_content(tmp_path, "") is None

    def test_blocks_path_traversal(self, sample_with_findings: tuple) -> None:
        _, _, sample_dir = sample_with_findings
        assert _read_svg_content(sample_dir, "../../etc/passwd") is None
        assert _read_svg_content(sample_dir, "../../../etc/shadow") is None


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
