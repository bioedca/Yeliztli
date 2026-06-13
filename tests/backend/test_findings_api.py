"""Tests for unified findings API (P3-39).

Covers:
- GET /api/analysis/findings — list all findings with filters
- GET /api/analysis/findings/summary — per-module counts + high confidence
- GET /api/analysis/findings/{id}/svg — SVG image retrieval
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import findings, reference_metadata, samples

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()
    return data_dir


@pytest.fixture
def findings_client(
    tmp_data_dir: Path,
) -> Generator[TestClient, None, None]:
    """FastAPI test client with a sample pre-seeded with findings."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    # Create reference.db with samples table
    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)

    # Create sample DB file on disk
    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)

    # Register sample in reference DB
    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1,
                name="Test Sample",
                db_path="samples/sample_1.db",
                file_format="v5",
                file_hash="abc123",
            )
        )

    # Seed findings individually (different columns per row)
    seed_findings = [
        {
            "module": "cancer",
            "category": "monogenic_variant",
            "evidence_level": 4,
            "gene_symbol": "BRCA1",
            "rsid": "rs80357906",
            "finding_text": "BRCA1 Pathogenic",
            "clinvar_significance": "Pathogenic",
            "pmid_citations": json.dumps(["12345678"]),
            "detail_json": json.dumps({"syndromes": ["HBOC"]}),
            "provenance": json.dumps(
                {
                    "pipeline_version": "0.2.0",
                    "pipeline_genome_build": "GRCh37",
                    "sources": {"clinvar": {"version": "2026-05-01", "genome_build": "GRCh37"}},
                    "variation_ids": {"rsid": "rs80357906"},
                    "annotation_coverage": 0b0000110,
                    "annotation_coverage_sources": ["ClinVar", "gnomAD"],
                }
            ),
        },
        {
            "module": "pharmacogenomics",
            "category": "prescribing_alert",
            "evidence_level": 4,
            "gene_symbol": "CYP2C19",
            "diplotype": "*1/*2",
            "metabolizer_status": "Intermediate Metabolizer",
            "drug": "clopidogrel",
            "finding_text": "CYP2C19 *1/*2 IM",
        },
        {
            "module": "nutrigenomics",
            "category": "pathway_summary",
            "evidence_level": 2,
            "finding_text": "Folate Metabolism - Elevated",
            "pathway": "Folate Metabolism",
            "pathway_level": "Elevated",
        },
        {
            "module": "ancestry",
            "category": "biogeographic",
            "evidence_level": 2,
            "finding_text": "82% European ancestry",
        },
        {
            "module": "carrier_status",
            "category": "monogenic_variant",
            "evidence_level": 3,
            "gene_symbol": "CFTR",
            "finding_text": "CFTR carrier",
        },
        {
            "module": "gene_health",
            "category": "disease_risk",
            "evidence_level": 3,
            "gene_symbol": "APOE",
            "finding_text": "Alzheimer's disease risk (APOE ε4)",
            "related_module": "apoe",
            "related_finding_id": 1,
        },
    ]
    with sample_engine.begin() as conn:
        for f in seed_findings:
            conn.execute(findings.insert().values(**f))

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


# ── List findings tests ─────────────────────────────────────────────


class TestListFindings:
    def test_list_all_findings(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 6

    def test_sorted_by_evidence_level_desc(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=1")
        data = resp.json()
        levels = [f["evidence_level"] for f in data]
        assert levels == sorted(levels, reverse=True)

    def test_filter_by_module(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=1&module=cancer")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["module"] == "cancer"

    def test_filter_by_min_stars(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=1&min_stars=3")
        data = resp.json()
        assert len(data) == 4
        for f in data:
            assert f["evidence_level"] >= 3

    def test_filter_by_category(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=1&category=prescribing_alert")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["category"] == "prescribing_alert"

    def test_invalid_sample_returns_404(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=999")
        assert resp.status_code == 404

    def test_finding_has_parsed_pmids(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=1&module=cancer")
        data = resp.json()
        assert data[0]["pmid_citations"] == ["12345678"]

    def test_finding_has_parsed_detail(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=1&module=cancer")
        data = resp.json()
        assert data[0]["detail"]["syndromes"] == ["HBOC"]

    def test_finding_has_parsed_provenance(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=1&module=cancer")
        prov = resp.json()[0]["provenance"]
        # Full audit-metadata contract is preserved end-to-end (not just a subset).
        assert set(prov) == {
            "pipeline_version",
            "pipeline_genome_build",
            "sources",
            "variation_ids",
            "annotation_coverage",
            "annotation_coverage_sources",
        }
        assert prov["sources"]["clinvar"]["version"] == "2026-05-01"
        assert prov["variation_ids"]["rsid"] == "rs80357906"
        assert prov["annotation_coverage_sources"] == ["ClinVar", "gnomAD"]

    def test_finding_without_provenance_is_none(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=1&module=pharmacogenomics")
        data = resp.json()
        assert data[0]["provenance"] is None

    def test_finding_has_cross_module_link(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=1&module=gene_health")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["related_module"] == "apoe"
        assert data[0]["related_finding_id"] == 1

    def test_finding_without_cross_link_has_null_fields(self, findings_client):
        resp = findings_client.get("/api/analysis/findings?sample_id=1&module=cancer")
        data = resp.json()
        assert data[0]["related_module"] is None
        assert data[0]["related_finding_id"] is None


# ── Summary tests ───────────────────────────────────────────────────


class TestFindingsSummary:
    def test_summary_returns_all_modules(self, findings_client):
        resp = findings_client.get("/api/analysis/findings/summary?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_findings"] == 6
        modules = {m["module"] for m in data["modules"]}
        assert "cancer" in modules
        assert "pharmacogenomics" in modules
        assert "nutrigenomics" in modules

    def test_summary_high_confidence(self, findings_client):
        resp = findings_client.get("/api/analysis/findings/summary?sample_id=1")
        data = resp.json()
        high_conf = data["high_confidence_findings"]
        assert len(high_conf) <= 5
        for f in high_conf:
            assert f["evidence_level"] >= 3

    def test_summary_module_counts(self, findings_client):
        resp = findings_client.get("/api/analysis/findings/summary?sample_id=1")
        data = resp.json()
        cancer_mod = next(m for m in data["modules"] if m["module"] == "cancer")
        assert cancer_mod["count"] == 1
        assert cancer_mod["max_evidence_level"] == 4


# ── SVG endpoint tests ─────────────────────────────────────────────


class TestFindingSvg:
    def test_no_svg_returns_404(self, findings_client):
        resp = findings_client.get("/api/analysis/findings/1/svg?sample_id=1")
        # svg_path is None for seeded findings
        assert resp.status_code == 404

    def test_nonexistent_finding_returns_404(self, findings_client):
        resp = findings_client.get("/api/analysis/findings/999/svg?sample_id=1")
        assert resp.status_code == 404


# ── APOE disclosure gate on the generic aggregator (issue #222) ──────


@pytest.fixture
def apoe_findings_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    """Test client with both APOE and non-APOE findings stored for one sample.

    The APOE gate is NOT acknowledged by default (no apoe_gate row), so the
    generic ``/api/analysis/findings`` aggregator must withhold the APOE rows.
    """
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)

    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)

    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1,
                name="Test Sample",
                db_path="samples/sample_1.db",
                file_format="v5",
                file_hash="abc123",
            )
        )

    seed_findings = [
        # Non-APOE finding — must always be visible.
        {
            "module": "cancer",
            "category": "monogenic_variant",
            "evidence_level": 4,
            "gene_symbol": "BRCA1",
            "finding_text": "BRCA1 Pathogenic",
        },
        # APOE findings — the ε4 diplotype + Alzheimer's narrative the gate protects.
        {
            "module": "apoe",
            "category": "genotype",
            "gene_symbol": "APOE",
            "finding_text": "APOE genotype determined",
            "diplotype": "ε3/ε4",
        },
        {
            "module": "apoe",
            "category": "cardiovascular_risk",
            "evidence_level": 4,
            "gene_symbol": "APOE",
            "finding_text": "APOE ε4 cardiovascular risk context",
            "diplotype": "ε3/ε4",
        },
        {
            "module": "apoe",
            "category": "alzheimers_risk",
            "evidence_level": 4,
            "gene_symbol": "APOE",
            "finding_text": "APOE ε3/ε4 — probabilistic Alzheimer's disease risk modifier",
            "diplotype": "ε3/ε4",
        },
        {
            "module": "apoe",
            "category": "lipid_dietary",
            "evidence_level": 3,
            "gene_symbol": "APOE",
            "finding_text": "APOE ε4 lipid/dietary context",
            "diplotype": "ε3/ε4",
        },
    ]
    with sample_engine.begin() as conn:
        for f in seed_findings:
            conn.execute(findings.insert().values(**f))

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


class TestAPOEGateOnGenericFindings:
    """The generic findings aggregator must honor the APOE disclosure gate (#222)."""

    def test_apoe_withheld_from_unfiltered_list_before_ack(self, apoe_findings_client):
        resp = apoe_findings_client.get("/api/analysis/findings?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        modules = {f["module"] for f in data}
        assert "apoe" not in modules
        # Non-APOE findings still surface.
        assert "cancer" in modules
        # Neither the diplotype nor the Alzheimer's narrative leaks.
        assert "ε3/ε4" not in resp.text
        assert "alzheimer" not in resp.text.lower()

    def test_apoe_withheld_from_explicit_module_filter_before_ack(self, apoe_findings_client):
        resp = apoe_findings_client.get("/api/analysis/findings?sample_id=1&module=apoe")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_apoe_withheld_from_summary_before_ack(self, apoe_findings_client):
        resp = apoe_findings_client.get("/api/analysis/findings/summary?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        modules = {m["module"] for m in data["modules"]}
        assert "apoe" not in modules
        # No APOE row leaks via top_finding_text or high_confidence_findings.
        assert "ε3/ε4" not in resp.text
        assert "alzheimer" not in resp.text.lower()
        assert all(f["module"] != "apoe" for f in data["high_confidence_findings"])

    def test_apoe_visible_in_list_after_ack(self, apoe_findings_client):
        ack = apoe_findings_client.post(
            "/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1}
        )
        assert ack.status_code == 200

        resp = apoe_findings_client.get("/api/analysis/findings?sample_id=1&module=apoe")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 4
        assert all(f["module"] == "apoe" for f in data)
        assert all(f["diplotype"] == "ε3/ε4" for f in data)

    def test_apoe_visible_in_summary_after_ack(self, apoe_findings_client):
        apoe_findings_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1})

        resp = apoe_findings_client.get("/api/analysis/findings/summary?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        apoe_mod = next((m for m in data["modules"] if m["module"] == "apoe"), None)
        assert apoe_mod is not None
        assert apoe_mod["count"] == 4
        # The high-evidence APOE rows re-enter high_confidence_findings post-ack —
        # guards against an over-gating regression that permanently drops them.
        assert any(f["module"] == "apoe" for f in data["high_confidence_findings"])

    def test_apoe_withheld_with_min_stars_filter_before_ack(self, apoe_findings_client):
        # min_stars combined with the APOE gate: the high-evidence APOE rows
        # (cardiovascular/alzheimers = 4★, lipid = 3★) must stay withheld pre-ack.
        resp = apoe_findings_client.get("/api/analysis/findings?sample_id=1&min_stars=3")
        assert resp.status_code == 200
        data = resp.json()
        assert {f["module"] for f in data} == {"cancer"}
        assert "ε3/ε4" not in resp.text

        apoe_findings_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1})
        resp = apoe_findings_client.get("/api/analysis/findings?sample_id=1&min_stars=3")
        data = resp.json()
        assert "apoe" in {f["module"] for f in data}


@pytest.fixture
def apoe_svg_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    """Client where an APOE finding and a non-APOE finding both have on-disk SVGs.

    The APOE SVG card renders the ε4 diplotype + risk label, so the by-id SVG
    endpoint must honor the same gate as list/summary (issue #222). Findings are
    inserted with explicit ids so the test can request them directly: id=1 is the
    APOE card (gated), id=2 is a non-APOE card (always served).
    """
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)

    sample_dir = tmp_data_dir / "samples"
    sample_db_path = sample_dir / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)

    # Write the on-disk SVGs the endpoint serves (relative to the sample dir).
    svg_dir = sample_dir / "svgs"
    svg_dir.mkdir(exist_ok=True)
    (svg_dir / "apoe_card.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><text>Diplotype: ε3/ε4</text></svg>',
        encoding="utf-8",
    )
    (svg_dir / "cancer_card.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><text>BRCA1</text></svg>',
        encoding="utf-8",
    )

    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1,
                name="Test Sample",
                db_path="samples/sample_1.db",
                file_format="v5",
                file_hash="abc123",
            )
        )
    with sample_engine.begin() as conn:
        conn.execute(
            findings.insert().values(
                id=1,
                module="apoe",
                category="alzheimers_risk",
                evidence_level=4,
                gene_symbol="APOE",
                finding_text="APOE ε3/ε4 — probabilistic Alzheimer's disease risk modifier",
                diplotype="ε3/ε4",
                svg_path="svgs/apoe_card.svg",
            )
        )
        conn.execute(
            findings.insert().values(
                id=2,
                module="cancer",
                category="monogenic_variant",
                evidence_level=4,
                gene_symbol="BRCA1",
                finding_text="BRCA1 Pathogenic",
                svg_path="svgs/cancer_card.svg",
            )
        )

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


class TestAPOESvgGate:
    """GET /findings/{id}/svg must honor the APOE disclosure gate (issue #222)."""

    def test_apoe_svg_withheld_before_ack(self, apoe_svg_client):
        # 404 (not 403) pre-ack: must not even confirm the APOE finding exists.
        resp = apoe_svg_client.get("/api/analysis/findings/1/svg?sample_id=1")
        assert resp.status_code == 404

    def test_non_apoe_svg_served_regardless(self, apoe_svg_client):
        # Non-APOE SVGs are unaffected by the gate.
        resp = apoe_svg_client.get("/api/analysis/findings/2/svg?sample_id=1")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/svg+xml")

    def test_apoe_svg_served_after_ack(self, apoe_svg_client):
        apoe_svg_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1})
        resp = apoe_svg_client.get("/api/analysis/findings/1/svg?sample_id=1")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/svg+xml")
        assert "ε3/ε4" in resp.text


@pytest.fixture
def aneuploidy_findings_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    """Test client with a gated sex-aneuploidy finding + a non-gated finding.

    The aneuploidy gate is NOT acknowledged by default (no aneuploidy_gate row),
    so the generic ``/api/analysis/findings`` aggregator must withhold the
    sex_aneuploidy row (issue #299).
    """
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)

    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)

    with ref_engine.begin() as conn:
        conn.execute(
            samples.insert().values(
                id=1,
                name="Test Sample",
                db_path="samples/sample_1.db",
                file_format="v5",
                file_hash="abc123",
            )
        )

    seed_findings = [
        # Non-gated finding — must always be visible.
        {
            "module": "cancer",
            "category": "monogenic_variant",
            "evidence_level": 4,
            "gene_symbol": "BRCA1",
            "finding_text": "BRCA1 Pathogenic",
        },
        # The gated sex-aneuploidy screen result (possible XXY / Klinefelter).
        {
            "module": "sex_aneuploidy",
            "category": "aneuploidy_screen",
            "evidence_level": 3,
            "finding_text": (
                "Screen suggests a possible sex-chromosome aneuploidy with an XXY "
                "(Klinefelter) pattern — confirmation required."
            ),
        },
    ]
    with sample_engine.begin() as conn:
        for f in seed_findings:
            conn.execute(findings.insert().values(**f))

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


class TestAneuploidyGateOnGenericFindings:
    """The generic findings aggregator must honor the sex-aneuploidy gate (#299)."""

    def test_withheld_from_unfiltered_list_before_ack(self, aneuploidy_findings_client):
        resp = aneuploidy_findings_client.get("/api/analysis/findings?sample_id=1")
        assert resp.status_code == 200
        modules = {f["module"] for f in resp.json()}
        assert "sex_aneuploidy" not in modules
        assert "cancer" in modules  # non-gated findings still surface
        # The XXY/Klinefelter screen text must not leak.
        assert "xxy" not in resp.text.lower()
        assert "klinefelter" not in resp.text.lower()

    def test_withheld_from_explicit_module_filter_before_ack(self, aneuploidy_findings_client):
        resp = aneuploidy_findings_client.get(
            "/api/analysis/findings?sample_id=1&module=sex_aneuploidy"
        )
        assert resp.status_code == 200  # empty list, not a 403 that confirms data exists
        assert resp.json() == []

    def test_withheld_from_summary_before_ack(self, aneuploidy_findings_client):
        resp = aneuploidy_findings_client.get("/api/analysis/findings/summary?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert "sex_aneuploidy" not in {m["module"] for m in data["modules"]}
        assert "xxy" not in resp.text.lower()
        assert "klinefelter" not in resp.text.lower()
        assert all(f["module"] != "sex_aneuploidy" for f in data["high_confidence_findings"])

    def test_visible_in_list_after_ack(self, aneuploidy_findings_client):
        ack = aneuploidy_findings_client.post(
            "/api/analysis/sex-aneuploidy/acknowledge-gate", params={"sample_id": 1}
        )
        assert ack.status_code == 200
        resp = aneuploidy_findings_client.get(
            "/api/analysis/findings?sample_id=1&module=sex_aneuploidy"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["module"] == "sex_aneuploidy"
        assert "XXY" in data[0]["finding_text"]

    def test_visible_in_summary_after_ack(self, aneuploidy_findings_client):
        aneuploidy_findings_client.post(
            "/api/analysis/sex-aneuploidy/acknowledge-gate", params={"sample_id": 1}
        )
        resp = aneuploidy_findings_client.get("/api/analysis/findings/summary?sample_id=1")
        assert resp.status_code == 200
        data = resp.json()
        assert "sex_aneuploidy" in {m["module"] for m in data["modules"]}
