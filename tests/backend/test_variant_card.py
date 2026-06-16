"""Tests for single-variant evidence card generator (P4-09).

Covers:
- T4-11: Variant evidence card generates valid PDF for known variant
- Single finding loading by ID
- HTML rendering with all metadata fields
- Module disclaimer inclusion
- SVG embedding
- API endpoint responses (PDF, PNG, preview)
- Error handling (missing sample, missing finding)
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
from backend.reports.variant_card import (
    _load_single_finding,
    render_variant_card_html,
)

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

    # Create SVG directory in the samples/ dir (parent of the .db file)
    # _get_sample_info returns sample_db_full.parent as sample_dir
    samples_dir = tmp_data_dir / "samples"
    svgs_dir = samples_dir / "svgs"
    svgs_dir.mkdir(exist_ok=True)
    (svgs_dir / "brca1.svg").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="40">'
        '<rect width="200" height="40" fill="#0D9488"/></svg>\n',
        encoding="utf-8",
    )

    # Seed findings
    seed_findings = [
        {
            "id": 1,
            "module": "cancer",
            "category": "monogenic_variant",
            "evidence_level": 4,
            "gene_symbol": "BRCA1",
            "rsid": "rs80357906",
            "finding_text": "BRCA1 Pathogenic variant for Hereditary Breast Cancer",
            "clinvar_significance": "Pathogenic",
            "zygosity": "heterozygous",
            "phenotype": "Hereditary Breast and Ovarian Cancer",
            "conditions": "Breast cancer, Ovarian cancer",
            "pmid_citations": json.dumps(["12345678", "87654321"]),
            "svg_path": "svgs/brca1.svg",
        },
        {
            "id": 2,
            "module": "pharmacogenomics",
            "category": "prescribing_alert",
            "evidence_level": 4,
            "gene_symbol": "CYP2C19",
            "rsid": "rs4244285",
            "diplotype": "*1/*2",
            "metabolizer_status": "Intermediate Metabolizer",
            "drug": "clopidogrel",
            "finding_text": "CYP2C19 *1/*2 — Intermediate Metabolizer for clopidogrel",
            "pmid_citations": json.dumps(["23698643"]),
        },
        {
            "id": 3,
            "module": "nutrigenomics",
            "category": "pathway_summary",
            "evidence_level": 2,
            "finding_text": "Folate Metabolism — Elevated consideration",
            "pathway": "Folate Metabolism",
            "pathway_level": "Elevated",
        },
        {
            "id": 4,
            "module": "cancer",
            "category": "prs",
            "evidence_level": 2,
            "finding_text": "Breast Cancer PRS: 72nd percentile",
            "prs_score": 0.45,
            "prs_percentile": 72.0,
        },
        {
            "id": 5,
            "module": "ancestry",
            "category": "haplogroup",
            "evidence_level": 2,
            "finding_text": "mtDNA Haplogroup: H1a1",
            "haplogroup": "H1a1",
        },
        {
            # #918: a CFTR carrier finding whose `conditions` is the raw CLNDN
            # blob — | separators, not provided/not specified placeholders, a
            # case-duplicate, and an ivacaftor drug-response (not a disease).
            "id": 6,
            "module": "carrier",
            "category": "carrier_variant",
            "evidence_level": 3,
            "gene_symbol": "CFTR",
            "rsid": "rs78655421",
            "finding_text": "CFTR carrier variant",
            "clinvar_significance": "Pathogenic",
            "zygosity": "heterozygous",
            "conditions": (
                "Respiratory ciliopathies including non-CF bronchiectasis|"
                "Cystic fibrosis|not provided|not specified|CFTR-related disorder|"
                "cystic fibrosis|Obstructive azoospermia|ivacaftor response - Efficacy"
            ),
            "pmid_citations": json.dumps(["11111111"]),
        },
        {
            # #963: a GATED-module finding. The APOE gate is unacknowledged by
            # default (no apoe_gate row), so this must not leak through any
            # variant-card endpoint until its opt-in gate is acknowledged.
            "id": 7,
            "module": "apoe",
            "category": "alzheimers_risk",
            "evidence_level": 3,
            "gene_symbol": "APOE",
            "finding_text": "APOE ε3/ε4 is associated with increased Alzheimer's risk",
            "phenotype": "Alzheimer's disease risk",
        },
    ]
    with sample_engine.begin() as conn:
        for f in seed_findings:
            conn.execute(findings.insert().values(**f))

    return ref_engine, sample_engine, sample_dir


@pytest.fixture
def card_client(
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


# ── Unit tests: single finding loading ───────────────────────────────


class TestLoadSingleFinding:
    """Test _load_single_finding helper."""

    def test_loads_finding_by_id(self, sample_with_findings: tuple) -> None:
        _, sample_engine, _ = sample_with_findings
        result = _load_single_finding(sample_engine, finding_id=1)
        assert result["id"] == 1
        assert result["gene_symbol"] == "BRCA1"
        assert result["rsid"] == "rs80357906"
        assert result["module"] == "cancer"
        assert result["evidence_level"] == 4

    def test_loads_pgx_finding(self, sample_with_findings: tuple) -> None:
        _, sample_engine, _ = sample_with_findings
        result = _load_single_finding(sample_engine, finding_id=2)
        assert result["diplotype"] == "*1/*2"
        assert result["metabolizer_status"] == "Intermediate Metabolizer"
        assert result["drug"] == "clopidogrel"

    def test_loads_nutrigenomics_finding(self, sample_with_findings: tuple) -> None:
        _, sample_engine, _ = sample_with_findings
        result = _load_single_finding(sample_engine, finding_id=3)
        assert result["pathway"] == "Folate Metabolism"
        assert result["pathway_level"] == "Elevated"

    def test_loads_prs_finding(self, sample_with_findings: tuple) -> None:
        _, sample_engine, _ = sample_with_findings
        result = _load_single_finding(sample_engine, finding_id=4)
        assert result["prs_score"] == 0.45
        assert result["prs_percentile"] == 72.0

    def test_loads_haplogroup_finding(self, sample_with_findings: tuple) -> None:
        _, sample_engine, _ = sample_with_findings
        result = _load_single_finding(sample_engine, finding_id=5)
        assert result["haplogroup"] == "H1a1"

    def test_pmid_citations_parsed(self, sample_with_findings: tuple) -> None:
        _, sample_engine, _ = sample_with_findings
        result = _load_single_finding(sample_engine, finding_id=1)
        assert result["pmid_citations"] == ["12345678", "87654321"]

    def test_missing_pmids_returns_empty_list(self, sample_with_findings: tuple) -> None:
        _, sample_engine, _ = sample_with_findings
        result = _load_single_finding(sample_engine, finding_id=3)
        assert result["pmid_citations"] == []

    def test_raises_for_missing_finding(self, sample_with_findings: tuple) -> None:
        _, sample_engine, _ = sample_with_findings
        with pytest.raises(ValueError, match="Finding 999 not found"):
            _load_single_finding(sample_engine, finding_id=999)

    def test_clinvar_conditions_cleaned(self, sample_with_findings: tuple) -> None:
        """#918: the raw CLNDN blob is cleaned for display — no | separators, no
        not provided/not specified placeholders, no drug-response entries, and
        case-duplicates collapsed — while real conditions survive."""
        _, sample_engine, _ = sample_with_findings
        result = _load_single_finding(sample_engine, finding_id=6)
        conditions = result["conditions"]

        assert "|" not in conditions
        assert "not provided" not in conditions
        assert "not specified" not in conditions
        assert "ivacaftor response - Efficacy" not in conditions
        # Real disease names survive, comma-joined, de-duped (one "Cystic fibrosis").
        assert "Cystic fibrosis" in conditions
        assert "CFTR-related disorder" in conditions
        assert "Obstructive azoospermia" in conditions
        assert conditions.lower().count("cystic fibrosis") == 1


# ── Unit tests: HTML rendering ───────────────────────────────────────


class TestVariantCardHtml:
    """Test render_variant_card_html."""

    def test_renders_brca1_card(self, tmp_data_dir: Path, sample_with_findings: tuple) -> None:
        ref_engine, _, _ = sample_with_findings
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        with (
            patch("backend.reports.generator.get_registry") as mock_reg,
            patch("backend.db.connection.get_settings", return_value=settings),
        ):
            registry = mock_reg.return_value
            registry.settings = settings
            registry.reference_engine = ref_engine

            sample_db = tmp_data_dir / "samples" / "sample_1.db"
            sample_engine = sa.create_engine(f"sqlite:///{sample_db}")
            registry.get_sample_engine.return_value = sample_engine

            html = render_variant_card_html(sample_id=1, finding_id=1)

        # Basic structure checks
        assert "Variant Evidence Card" in html
        assert "Test Patient" in html
        assert "BRCA1" in html
        assert "rs80357906" in html
        assert "Pathogenic variant for Hereditary Breast Cancer" in html

    def test_conditions_row_renders_cleaned_blob(
        self, tmp_data_dir: Path, sample_with_findings: tuple
    ) -> None:
        """#918: the rendered card's Conditions row shows cleaned disease names,
        never the raw CLNDN blob (| separators / placeholders / drug-response)."""
        ref_engine, _, _ = sample_with_findings
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        with (
            patch("backend.reports.generator.get_registry") as mock_reg,
            patch("backend.db.connection.get_settings", return_value=settings),
        ):
            registry = mock_reg.return_value
            registry.settings = settings
            registry.reference_engine = ref_engine

            sample_db = tmp_data_dir / "samples" / "sample_1.db"
            sample_engine = sa.create_engine(f"sqlite:///{sample_db}")
            registry.get_sample_engine.return_value = sample_engine

            html = render_variant_card_html(sample_id=1, finding_id=6)

        assert "Conditions" in html
        assert "Cystic fibrosis" in html
        # The raw-blob artifacts must not reach the rendered report.
        assert "not provided" not in html
        assert "not specified" not in html
        assert "ivacaftor response - Efficacy" not in html

    def test_contains_evidence_stars(
        self, tmp_data_dir: Path, sample_with_findings: tuple
    ) -> None:
        ref_engine, _, _ = sample_with_findings
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        with (
            patch("backend.reports.generator.get_registry") as mock_reg,
            patch("backend.db.connection.get_settings", return_value=settings),
        ):
            registry = mock_reg.return_value
            registry.settings = settings
            registry.reference_engine = ref_engine

            sample_db = tmp_data_dir / "samples" / "sample_1.db"
            sample_engine = sa.create_engine(f"sqlite:///{sample_db}")
            registry.get_sample_engine.return_value = sample_engine

            html = render_variant_card_html(sample_id=1, finding_id=1)

        assert "evidence-stars" in html
        assert "4 out of 4 stars" in html

    def test_contains_clinvar_badge(self, tmp_data_dir: Path, sample_with_findings: tuple) -> None:
        ref_engine, _, _ = sample_with_findings
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        with (
            patch("backend.reports.generator.get_registry") as mock_reg,
            patch("backend.db.connection.get_settings", return_value=settings),
        ):
            registry = mock_reg.return_value
            registry.settings = settings
            registry.reference_engine = ref_engine

            sample_db = tmp_data_dir / "samples" / "sample_1.db"
            sample_engine = sa.create_engine(f"sqlite:///{sample_db}")
            registry.get_sample_engine.return_value = sample_engine

            html = render_variant_card_html(sample_id=1, finding_id=1)

        assert "badge-clinvar" in html
        assert "Pathogenic" in html

    def test_contains_zygosity(self, tmp_data_dir: Path, sample_with_findings: tuple) -> None:
        ref_engine, _, _ = sample_with_findings
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        with (
            patch("backend.reports.generator.get_registry") as mock_reg,
            patch("backend.db.connection.get_settings", return_value=settings),
        ):
            registry = mock_reg.return_value
            registry.settings = settings
            registry.reference_engine = ref_engine

            sample_db = tmp_data_dir / "samples" / "sample_1.db"
            sample_engine = sa.create_engine(f"sqlite:///{sample_db}")
            registry.get_sample_engine.return_value = sample_engine

            html = render_variant_card_html(sample_id=1, finding_id=1)

        assert "heterozygous" in html
        assert "Zygosity" in html

    def test_contains_pmid_references(
        self, tmp_data_dir: Path, sample_with_findings: tuple
    ) -> None:
        ref_engine, _, _ = sample_with_findings
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        with (
            patch("backend.reports.generator.get_registry") as mock_reg,
            patch("backend.db.connection.get_settings", return_value=settings),
        ):
            registry = mock_reg.return_value
            registry.settings = settings
            registry.reference_engine = ref_engine

            sample_db = tmp_data_dir / "samples" / "sample_1.db"
            sample_engine = sa.create_engine(f"sqlite:///{sample_db}")
            registry.get_sample_engine.return_value = sample_engine

            html = render_variant_card_html(sample_id=1, finding_id=1)

        assert "PMID:12345678" in html
        assert "PMID:87654321" in html
        assert "pubmed.ncbi.nlm.nih.gov" in html

    def test_contains_svg_content(self, tmp_data_dir: Path, sample_with_findings: tuple) -> None:
        ref_engine, _, _ = sample_with_findings
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        with (
            patch("backend.reports.generator.get_registry") as mock_reg,
            patch("backend.db.connection.get_settings", return_value=settings),
        ):
            registry = mock_reg.return_value
            registry.settings = settings
            registry.reference_engine = ref_engine

            sample_db = tmp_data_dir / "samples" / "sample_1.db"
            sample_engine = sa.create_engine(f"sqlite:///{sample_db}")
            registry.get_sample_engine.return_value = sample_engine

            html = render_variant_card_html(sample_id=1, finding_id=1)

        assert "svg-section" in html
        assert "<svg" in html
        assert "Visualization" in html

    def test_contains_module_disclaimer(
        self, tmp_data_dir: Path, sample_with_findings: tuple
    ) -> None:
        ref_engine, _, _ = sample_with_findings
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        with (
            patch("backend.reports.generator.get_registry") as mock_reg,
            patch("backend.db.connection.get_settings", return_value=settings),
        ):
            registry = mock_reg.return_value
            registry.settings = settings
            registry.reference_engine = ref_engine

            sample_db = tmp_data_dir / "samples" / "sample_1.db"
            sample_engine = sa.create_engine(f"sqlite:///{sample_db}")
            registry.get_sample_engine.return_value = sample_engine

            html = render_variant_card_html(sample_id=1, finding_id=1)

        # Cancer module has a disclaimer
        assert "card-disclaimer" in html

    def test_contains_module_display_name(
        self, tmp_data_dir: Path, sample_with_findings: tuple
    ) -> None:
        ref_engine, _, _ = sample_with_findings
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        with (
            patch("backend.reports.generator.get_registry") as mock_reg,
            patch("backend.db.connection.get_settings", return_value=settings),
        ):
            registry = mock_reg.return_value
            registry.settings = settings
            registry.reference_engine = ref_engine

            sample_db = tmp_data_dir / "samples" / "sample_1.db"
            sample_engine = sa.create_engine(f"sqlite:///{sample_db}")
            registry.get_sample_engine.return_value = sample_engine

            html = render_variant_card_html(sample_id=1, finding_id=1)

        assert "Cancer Predisposition" in html

    def test_pgx_card_shows_diplotype_and_drug(
        self, tmp_data_dir: Path, sample_with_findings: tuple
    ) -> None:
        ref_engine, _, _ = sample_with_findings
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        with (
            patch("backend.reports.generator.get_registry") as mock_reg,
            patch("backend.db.connection.get_settings", return_value=settings),
        ):
            registry = mock_reg.return_value
            registry.settings = settings
            registry.reference_engine = ref_engine

            sample_db = tmp_data_dir / "samples" / "sample_1.db"
            sample_engine = sa.create_engine(f"sqlite:///{sample_db}")
            registry.get_sample_engine.return_value = sample_engine

            html = render_variant_card_html(sample_id=1, finding_id=2)

        assert "CYP2C19" in html
        assert "*1/*2" in html
        assert "clopidogrel" in html
        assert "Intermediate Metabolizer" in html
        assert "Pharmacogenomics" in html

    def test_nutrigenomics_card_shows_pathway_badge(
        self, tmp_data_dir: Path, sample_with_findings: tuple
    ) -> None:
        ref_engine, _, _ = sample_with_findings
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        with (
            patch("backend.reports.generator.get_registry") as mock_reg,
            patch("backend.db.connection.get_settings", return_value=settings),
        ):
            registry = mock_reg.return_value
            registry.settings = settings
            registry.reference_engine = ref_engine

            sample_db = tmp_data_dir / "samples" / "sample_1.db"
            sample_engine = sa.create_engine(f"sqlite:///{sample_db}")
            registry.get_sample_engine.return_value = sample_engine

            html = render_variant_card_html(sample_id=1, finding_id=3)

        assert "badge-elevated" in html
        assert "Folate Metabolism" in html

    def test_prs_card_shows_percentile(
        self, tmp_data_dir: Path, sample_with_findings: tuple
    ) -> None:
        ref_engine, _, _ = sample_with_findings
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        with (
            patch("backend.reports.generator.get_registry") as mock_reg,
            patch("backend.db.connection.get_settings", return_value=settings),
        ):
            registry = mock_reg.return_value
            registry.settings = settings
            registry.reference_engine = ref_engine

            sample_db = tmp_data_dir / "samples" / "sample_1.db"
            sample_engine = sa.create_engine(f"sqlite:///{sample_db}")
            registry.get_sample_engine.return_value = sample_engine

            html = render_variant_card_html(sample_id=1, finding_id=4)

        assert "72.0%" in html
        assert "0.4500" in html

    def test_haplogroup_card(self, tmp_data_dir: Path, sample_with_findings: tuple) -> None:
        ref_engine, _, _ = sample_with_findings
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        with (
            patch("backend.reports.generator.get_registry") as mock_reg,
            patch("backend.db.connection.get_settings", return_value=settings),
        ):
            registry = mock_reg.return_value
            registry.settings = settings
            registry.reference_engine = ref_engine

            sample_db = tmp_data_dir / "samples" / "sample_1.db"
            sample_engine = sa.create_engine(f"sqlite:///{sample_db}")
            registry.get_sample_engine.return_value = sample_engine

            html = render_variant_card_html(sample_id=1, finding_id=5)

        assert "H1a1" in html
        assert "Haplogroup" in html

    def test_contains_footer(self, tmp_data_dir: Path, sample_with_findings: tuple) -> None:
        ref_engine, _, _ = sample_with_findings
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        with (
            patch("backend.reports.generator.get_registry") as mock_reg,
            patch("backend.db.connection.get_settings", return_value=settings),
        ):
            registry = mock_reg.return_value
            registry.settings = settings
            registry.reference_engine = ref_engine

            sample_db = tmp_data_dir / "samples" / "sample_1.db"
            sample_engine = sa.create_engine(f"sqlite:///{sample_db}")
            registry.get_sample_engine.return_value = sample_engine

            html = render_variant_card_html(sample_id=1, finding_id=1)

        assert "card-footer" in html
        assert "Variant Evidence Card" in html
        assert "educational and research purposes" in html

    def test_contains_print_css(self, tmp_data_dir: Path, sample_with_findings: tuple) -> None:
        ref_engine, _, _ = sample_with_findings
        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        with (
            patch("backend.reports.generator.get_registry") as mock_reg,
            patch("backend.db.connection.get_settings", return_value=settings),
        ):
            registry = mock_reg.return_value
            registry.settings = settings
            registry.reference_engine = ref_engine

            sample_db = tmp_data_dir / "samples" / "sample_1.db"
            sample_engine = sa.create_engine(f"sqlite:///{sample_db}")
            registry.get_sample_engine.return_value = sample_engine

            html = render_variant_card_html(sample_id=1, finding_id=1)

        assert "@media print" in html
        assert "print-color-adjust" in html

    def test_raises_for_missing_sample(self, tmp_data_dir: Path) -> None:
        """Should raise ValueError for nonexistent sample."""
        ref_path = tmp_data_dir / "reference.db"
        ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
        reference_metadata.create_all(ref_engine)

        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        with (
            patch("backend.reports.generator.get_registry") as mock_reg,
            patch("backend.db.connection.get_settings", return_value=settings),
        ):
            registry = mock_reg.return_value
            registry.settings = settings
            registry.reference_engine = ref_engine

            with pytest.raises(ValueError, match="Sample 999 not found"):
                render_variant_card_html(sample_id=999, finding_id=1)


# ── API endpoint tests ──────────────────────────────────────────────


class TestVariantCardAPI:
    """Test variant card API endpoints."""

    def test_preview_returns_html(self, card_client: TestClient) -> None:
        resp = card_client.post(
            "/api/reports/variant-card/preview",
            json={"sample_id": 1, "finding_id": 1},
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "BRCA1" in resp.text
        assert "Variant Evidence Card" in resp.text

    def test_preview_finding_not_found(self, card_client: TestClient) -> None:
        resp = card_client.post(
            "/api/reports/variant-card/preview",
            json={"sample_id": 1, "finding_id": 999},
        )
        assert resp.status_code == 404

    def test_preview_sample_not_found(self, card_client: TestClient) -> None:
        resp = card_client.post(
            "/api/reports/variant-card/preview",
            json={"sample_id": 999, "finding_id": 1},
        )
        assert resp.status_code == 404

    def test_generate_pdf_endpoint_with_mock(self, card_client: TestClient) -> None:
        """T4-11: the variant-card endpoint streams the generator's PDF bytes.

        Scope: this validates the endpoint *plumbing* — bytes from
        ``generate_variant_card_pdf`` come back with the right content-type and
        filename. The PDF rendering itself is Playwright/Chromium-based
        (``backend/reports/variant_card.py``) and belongs to the E2E tier, not this
        fast unit test, so the generator is mocked here deliberately.
        """
        fake_pdf = b"%PDF-1.4 fake pdf content"
        with patch(
            "backend.reports.variant_card.generate_variant_card_pdf",
            new_callable=AsyncMock,
            return_value=fake_pdf,
        ):
            resp = card_client.post(
                "/api/reports/variant-card",
                json={"sample_id": 1, "finding_id": 1},
            )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert "variant_card_1_1.pdf" in resp.headers["content-disposition"]
        assert resp.content == fake_pdf

    def test_generate_png_endpoint_with_mock(self, card_client: TestClient) -> None:
        fake_png = b"\x89PNG\r\n\x1a\n fake png content"
        with patch(
            "backend.reports.variant_card.generate_variant_card_png",
            new_callable=AsyncMock,
            return_value=fake_png,
        ):
            resp = card_client.post(
                "/api/reports/variant-card/png",
                json={"sample_id": 1, "finding_id": 1},
            )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert "variant_card_1_1.png" in resp.headers["content-disposition"]
        assert resp.content == fake_png

    def test_pdf_endpoint_sample_not_found(self, card_client: TestClient) -> None:
        with patch(
            "backend.reports.variant_card.generate_variant_card_pdf",
            new_callable=AsyncMock,
            side_effect=ValueError("Sample 999 not found"),
        ):
            resp = card_client.post(
                "/api/reports/variant-card",
                json={"sample_id": 999, "finding_id": 1},
            )
        assert resp.status_code == 404

    def test_pdf_endpoint_playwright_missing(self, card_client: TestClient) -> None:
        with patch(
            "backend.reports.variant_card.generate_variant_card_pdf",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Playwright is required"),
        ):
            resp = card_client.post(
                "/api/reports/variant-card",
                json={"sample_id": 1, "finding_id": 1},
            )
        assert resp.status_code == 503

    def test_png_endpoint_sample_not_found(self, card_client: TestClient) -> None:
        with patch(
            "backend.reports.variant_card.generate_variant_card_png",
            new_callable=AsyncMock,
            side_effect=ValueError("Sample 999 not found"),
        ):
            resp = card_client.post(
                "/api/reports/variant-card/png",
                json={"sample_id": 999, "finding_id": 1},
            )
        assert resp.status_code == 404


# ── #963: disclosure-gate hardening of the variant-card endpoints ─────


class TestDisclosureGate:
    """A gated-module finding (APOE/Parkinson's/aneuploidy) must not leak via the
    enumerable finding_id variant-card routes before its opt-in gate is
    acknowledged — the un-hardened twin of the findings SVG-by-id gate (#963).

    The gate lives in ``_load_single_finding`` so all three routes (PDF/PNG/HTML
    preview) inherit it. Pre-ack it raises the same not-found error as a missing
    id → 404 (no-leak posture); post-ack the card renders normally.
    """

    # The APOE finding seeded by sample_with_findings (gated, unacknowledged).
    GATED_FINDING_ID = 7
    NONGATED_FINDING_ID = 1  # the cancer/BRCA1 finding

    def test_load_withholds_gated_finding_pre_ack(
        self, sample_with_findings: tuple[sa.Engine, sa.Engine, Path]
    ) -> None:
        _ref, sample_engine, _dir = sample_with_findings
        # No apoe_gate row → APOE is gated → load must refuse (same error as missing).
        with pytest.raises(ValueError, match="not found"):
            _load_single_finding(sample_engine, finding_id=self.GATED_FINDING_ID)

    def test_load_allows_nongated_finding(
        self, sample_with_findings: tuple[sa.Engine, sa.Engine, Path]
    ) -> None:
        _ref, sample_engine, _dir = sample_with_findings
        result = _load_single_finding(sample_engine, finding_id=self.NONGATED_FINDING_ID)
        assert result["module"] == "cancer"

    def test_load_allows_gated_finding_after_ack(
        self, sample_with_findings: tuple[sa.Engine, sa.Engine, Path]
    ) -> None:
        from datetime import UTC, datetime

        from backend.db.tables import apoe_gate

        _ref, sample_engine, _dir = sample_with_findings
        with sample_engine.begin() as conn:
            conn.execute(
                apoe_gate.insert().values(acknowledged=True, acknowledged_at=datetime.now(UTC))
            )
        result = _load_single_finding(sample_engine, finding_id=self.GATED_FINDING_ID)
        assert result["module"] == "apoe"

    def test_pdf_endpoint_404s_gated_finding_pre_ack(self, card_client: TestClient) -> None:
        resp = card_client.post(
            "/api/reports/variant-card",
            json={"sample_id": 1, "finding_id": self.GATED_FINDING_ID},
        )
        assert resp.status_code == 404

    def test_png_endpoint_404s_gated_finding_pre_ack(self, card_client: TestClient) -> None:
        resp = card_client.post(
            "/api/reports/variant-card/png",
            json={"sample_id": 1, "finding_id": self.GATED_FINDING_ID},
        )
        assert resp.status_code == 404

    def test_preview_endpoint_404s_gated_finding_pre_ack(self, card_client: TestClient) -> None:
        resp = card_client.post(
            "/api/reports/variant-card/preview",
            json={"sample_id": 1, "finding_id": self.GATED_FINDING_ID},
        )
        assert resp.status_code == 404

    def test_preview_endpoint_renders_nongated_finding(self, card_client: TestClient) -> None:
        # Sanity: the gate must not over-block a non-gated finding.
        resp = card_client.post(
            "/api/reports/variant-card/preview",
            json={"sample_id": 1, "finding_id": self.NONGATED_FINDING_ID},
        )
        assert resp.status_code == 200

    def test_preview_endpoint_renders_gated_finding_after_ack(
        self, card_client: TestClient
    ) -> None:
        ack = card_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1})
        assert ack.status_code in (200, 204)
        resp = card_client.post(
            "/api/reports/variant-card/preview",
            json={"sample_id": 1, "finding_id": self.GATED_FINDING_ID},
        )
        assert resp.status_code == 200
        assert "APOE" in resp.text
