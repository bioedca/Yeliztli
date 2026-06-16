"""Tests for SVG rendering at analysis time (P3-39).

Covers:
- Individual SVG renderers (PRS gauge, pathway, metabolizer, etc.)
- Dispatcher routing
- Bulk save_finding_svgs writes files and updates svg_path
- generate_svgs_for_sample reads findings table and updates svg_path column
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from backend.analysis.svg_renderer import (
    generate_svgs_for_sample,
    render_finding_svg,
    save_finding_svgs,
)
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import findings

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def sample_engine():
    """In-memory sample DB with tables created."""
    engine = sa.create_engine("sqlite://")
    create_sample_tables(engine)
    return engine


@pytest.fixture
def prs_finding() -> dict:
    """A PRS finding dict."""
    return {
        "id": 1,
        "module": "cancer",
        "category": "prs",
        "evidence_level": 1,
        "finding_text": "Breast Cancer: 72nd percentile",
        "prs_percentile": 72.0,
        "detail_json": json.dumps(
            {
                "trait": "breast_cancer",
                "name": "Breast Cancer (BCAC)",
                "z_score": 0.58,
                "bootstrap_ci_lower": 65.0,
                "bootstrap_ci_upper": 79.0,
            }
        ),
    }


@pytest.fixture
def nutrigenomics_finding() -> dict:
    """A nutrigenomics pathway finding dict."""
    return {
        "id": 2,
        "module": "nutrigenomics",
        "category": "pathway_summary",
        "evidence_level": 2,
        "finding_text": "Folate Metabolism - Elevated consideration",
        "pathway": "Folate Metabolism",
        "pathway_level": "Elevated",
    }


@pytest.fixture
def pharmacogenomics_finding() -> dict:
    """A pharmacogenomics finding dict."""
    return {
        "id": 3,
        "module": "pharmacogenomics",
        "category": "prescribing_alert",
        "evidence_level": 4,
        "gene_symbol": "CYP2C19",
        "diplotype": "*1/*2",
        "metabolizer_status": "Intermediate Metabolizer",
        "drug": "clopidogrel",
        "finding_text": "CYP2C19 *1/*2 Intermediate Metabolizer",
    }


@pytest.fixture
def carrier_finding() -> dict:
    """A carrier status finding dict."""
    return {
        "id": 4,
        "module": "carrier",
        "category": "monogenic_variant",
        "evidence_level": 4,
        "gene_symbol": "CFTR",
        "rsid": "rs75527207",
        "finding_text": "CFTR carrier (heterozygous)",
        "conditions": "Cystic fibrosis",
        "zygosity": "het",
    }


@pytest.fixture
def apoe_finding() -> dict:
    """An APOE finding dict."""
    return {
        "id": 5,
        "module": "apoe",
        "category": "genotype",
        "evidence_level": 4,
        "gene_symbol": "APOE",
        "finding_text": "APOE genotype: e3/e4",
        "detail_json": json.dumps(
            {
                "diplotype": "\u03b53/\u03b54",
                "risk_level": "Elevated",
            }
        ),
    }


@pytest.fixture
def ancestry_finding() -> dict:
    """An ancestry admixture finding dict."""
    return {
        "id": 6,
        "module": "ancestry",
        "category": "admixture",
        "evidence_level": 2,
        "finding_text": "Biogeographic ancestry composition",
        "detail_json": json.dumps(
            {
                "fractions": {
                    "EUR": 0.82,
                    "AMR": 0.11,
                    "EAS": 0.07,
                },
            }
        ),
    }


# ── Dispatcher tests ────────────────────────────────────────────────


class TestRenderFindingSvg:
    """Test the dispatcher routes to correct renderers."""

    def test_prs_finding_generates_gauge(self, prs_finding):
        svg = render_finding_svg(prs_finding)
        assert svg is not None
        assert "<svg" in svg
        assert "72" in svg  # percentile value
        assert "95% CI: 65 – 79" in svg
        assert 'stroke="#14B8A6"' in svg
        assert 'opacity="0.35"' in svg
        assert "</svg>" in svg

    def test_prs_gauge_without_bootstrap_ci_omits_ci_band(self, prs_finding):
        detail = json.loads(prs_finding["detail_json"])
        detail.pop("bootstrap_ci_lower")
        detail.pop("bootstrap_ci_upper")
        prs_finding["detail_json"] = json.dumps(detail)

        svg = render_finding_svg(prs_finding)

        assert svg is not None
        assert "95% CI" not in svg
        assert 'stroke="#14B8A6"' not in svg
        assert 'opacity="0.35"' not in svg

    def test_prs_gauge_with_collapsed_bootstrap_ci_omits_ci_band(self, prs_finding):
        detail = json.loads(prs_finding["detail_json"])
        detail["bootstrap_ci_lower"] = 72.0
        detail["bootstrap_ci_upper"] = 72.0
        prs_finding["detail_json"] = json.dumps(detail)

        svg = render_finding_svg(prs_finding)

        assert svg is not None
        assert "95% CI" not in svg
        assert 'stroke="#14B8A6"' not in svg
        assert 'opacity="0.35"' not in svg

    def test_prs_gauge_with_malformed_bootstrap_ci_omits_ci_band(self, prs_finding):
        detail = json.loads(prs_finding["detail_json"])
        detail["bootstrap_ci_lower"] = "not-a-number"
        prs_finding["detail_json"] = json.dumps(detail)

        svg = render_finding_svg(prs_finding)

        assert svg is not None
        assert "95% CI" not in svg
        assert 'stroke="#14B8A6"' not in svg
        assert 'opacity="0.35"' not in svg

    def test_prs_gauge_with_withheld_percentile_returns_none(self, prs_finding):
        prs_finding["finding_text"] = (
            "Breast Cancer: population percentile not reported — "
            "score lacks a validated reference distribution"
        )
        prs_finding["prs_percentile"] = None

        svg = render_finding_svg(prs_finding)

        assert svg is None

    def test_nutrigenomics_generates_pathway_indicator(self, nutrigenomics_finding):
        svg = render_finding_svg(nutrigenomics_finding)
        assert svg is not None
        assert "<svg" in svg
        assert "Elevated" in svg or "Folate" in svg

    def test_pharmacogenomics_generates_metabolizer_card(self, pharmacogenomics_finding):
        svg = render_finding_svg(pharmacogenomics_finding)
        assert svg is not None
        assert "<svg" in svg
        assert "CYP2C19" in svg

    def test_carrier_generates_card(self, carrier_finding):
        svg = render_finding_svg(carrier_finding)
        assert svg is not None
        assert "<svg" in svg
        assert "CFTR" in svg

    def test_apoe_generates_card(self, apoe_finding):
        svg = render_finding_svg(apoe_finding)
        assert svg is not None
        assert "<svg" in svg

    def test_ancestry_admixture_generates_bar(self, ancestry_finding):
        svg = render_finding_svg(ancestry_finding)
        assert svg is not None
        assert "<svg" in svg
        assert "EUR" in svg

    def test_unknown_module_with_evidence_renders_stars(self):
        finding = {
            "module": "unknown_module",
            "category": "unknown",
            "evidence_level": 3,
        }
        svg = render_finding_svg(finding)
        assert svg is not None
        assert "<svg" in svg
        # Should have star polygons
        assert "polygon" in svg

    def test_unknown_module_no_evidence_returns_none(self):
        finding = {"module": "unknown_module", "category": "unknown"}
        svg = render_finding_svg(finding)
        assert svg is None

    def test_prs_svg_has_xml_header(self, prs_finding):
        svg = render_finding_svg(prs_finding)
        assert svg.startswith("<?xml")

    def test_prs_svg_has_teal_color(self, prs_finding):
        svg = render_finding_svg(prs_finding)
        # Primary teal should appear in the gauge
        assert "#0D9488" in svg or "#0d9488" in svg.lower()


# ── save_finding_svgs tests ─────────────────────────────────────────


class TestSaveFindingSvgs:
    """Test bulk SVG generation and file writing."""

    def test_saves_svg_files_to_disk(self, tmp_path, prs_finding):
        updated = save_finding_svgs([prs_finding], tmp_path)
        assert len(updated) == 1
        assert updated[0]["svg_path"] is not None
        svg_file = tmp_path / "svgs" / f"{prs_finding['id']}.svg"
        assert svg_file.exists()
        content = svg_file.read_text()
        assert "<svg" in content

    def test_creates_svgs_directory(self, tmp_path, prs_finding):
        save_finding_svgs([prs_finding], tmp_path)
        assert (tmp_path / "svgs").is_dir()

    def test_multiple_findings(self, tmp_path, prs_finding, nutrigenomics_finding):
        findings_list = [prs_finding, nutrigenomics_finding]
        updated = save_finding_svgs(findings_list, tmp_path)
        assert len(updated) == 2
        for f in updated:
            assert f["svg_path"] is not None

    def test_finding_without_svg_gets_none(self, tmp_path):
        finding = {
            "id": 99,
            "module": "unknown",
            "category": "unknown",
        }
        updated = save_finding_svgs([finding], tmp_path)
        assert updated[0].get("svg_path") is None

    def test_svg_path_is_relative_to_sample_dir(self, tmp_path, prs_finding):
        updated = save_finding_svgs([prs_finding], tmp_path)
        svg_path = updated[0]["svg_path"]
        assert svg_path is not None
        # Relative for portability (e.g. "svgs/1.svg")
        assert svg_path.startswith("svgs/")
        # But the file exists on disk
        assert (tmp_path / svg_path).exists()

    def test_withheld_prs_percentile_does_not_save_quantitative_gauge(self, tmp_path, prs_finding):
        prs_finding["prs_percentile"] = None

        updated = save_finding_svgs([prs_finding], tmp_path)

        assert updated[0].get("svg_path") is None
        assert not (tmp_path / "svgs" / f"{prs_finding['id']}.svg").exists()


# ── generate_svgs_for_sample tests ──────────────────────────────────


class TestGenerateSvgsForSample:
    """Test the full pipeline: read findings, generate SVGs, update DB."""

    def test_generates_svgs_and_updates_db(self, sample_engine, tmp_path):
        # Insert a finding into the DB
        with sample_engine.begin() as conn:
            conn.execute(
                findings.insert().values(
                    module="cancer",
                    category="prs",
                    evidence_level=1,
                    finding_text="Breast Cancer: 72nd percentile",
                    prs_percentile=72.0,
                    detail_json=json.dumps(
                        {
                            "trait": "breast_cancer",
                            "name": "Breast Cancer",
                            "z_score": 0.58,
                            "bootstrap_ci_lower": 65.0,
                            "bootstrap_ci_upper": 79.0,
                        }
                    ),
                )
            )

        count = generate_svgs_for_sample(sample_engine, tmp_path)
        assert count >= 1

        # Verify svg_path was updated in the DB
        with sample_engine.connect() as conn:
            rows = conn.execute(sa.select(findings)).fetchall()
            assert len(rows) == 1
            assert rows[0].svg_path is not None
            # svg_path is relative; file exists under sample_dir
            svg_path = tmp_path / rows[0].svg_path
            assert svg_path.exists()
            assert "95% CI: 65 – 79" in svg_path.read_text()

    def test_withheld_prs_percentile_does_not_update_svg_path(self, sample_engine, tmp_path):
        with sample_engine.begin() as conn:
            conn.execute(
                findings.insert().values(
                    module="cancer",
                    category="prs",
                    evidence_level=1,
                    finding_text=(
                        "Breast Cancer: population percentile not reported — "
                        "score lacks a validated reference distribution"
                    ),
                    prs_percentile=None,
                    detail_json=json.dumps(
                        {
                            "trait": "breast_cancer",
                            "name": "Breast Cancer",
                            "z_score": 0.58,
                        }
                    ),
                )
            )

        count = generate_svgs_for_sample(sample_engine, tmp_path)

        assert count == 0
        with sample_engine.connect() as conn:
            row = conn.execute(sa.select(findings)).one()
        assert row.svg_path is None
        assert not (tmp_path / "svgs" / f"{row.id}.svg").exists()

    def test_empty_findings_returns_zero(self, sample_engine, tmp_path):
        count = generate_svgs_for_sample(sample_engine, tmp_path)
        assert count == 0

    def test_multiple_modules_get_svgs(self, sample_engine, tmp_path):
        with sample_engine.begin() as conn:
            conn.execute(
                findings.insert().values(
                    module="pharmacogenomics",
                    category="prescribing_alert",
                    evidence_level=4,
                    gene_symbol="CYP2C19",
                    diplotype="*1/*2",
                    metabolizer_status="Intermediate Metabolizer",
                    drug="clopidogrel",
                    finding_text="CYP2C19 IM",
                )
            )
            conn.execute(
                findings.insert().values(
                    module="carrier",
                    category="monogenic_variant",
                    evidence_level=4,
                    gene_symbol="CFTR",
                    finding_text="CFTR carrier",
                    conditions="Cystic fibrosis",
                )
            )

        count = generate_svgs_for_sample(sample_engine, tmp_path)
        assert count == 2

        # Both findings should have svg_path set
        with sample_engine.connect() as conn:
            rows = conn.execute(sa.select(findings)).fetchall()
            assert len(rows) == 2
            for row in rows:
                assert row.svg_path is not None
