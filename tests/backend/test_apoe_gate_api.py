"""Tests for APOE ε4 opt-in disclosure gate API (P3-22c).

Covers:
  - GET /api/analysis/apoe/disclaimer — Gate disclosure text
  - GET /api/analysis/apoe/gate-status — Gate acknowledgment check
  - POST /api/analysis/apoe/acknowledge-gate — Gate acknowledgment
  - GET /api/analysis/apoe/genotype — Basic genotype (not gate-protected)
  - GET /api/analysis/apoe/findings — Findings (gate-protected, 403 before ack)
  - POST /api/analysis/apoe/run — Run APOE analysis
  - T3-20: Gate blocks ε4 disclosure until explicit acknowledgment
  - Property invariant: findings must not appear before gate acknowledgment
"""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import (
    raw_variants,
    reference_metadata,
    samples,
)
from backend.disclaimers import (
    APOE_GATE_ACCEPT_LABEL,
    APOE_GATE_DECLINE_LABEL,
    APOE_GATE_TEXT,
    APOE_GATE_TITLE,
)

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory with samples subdir."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()
    return data_dir


def _create_sample_with_apoe(
    tmp_data_dir: Path,
    settings: Settings,
    rs429358_gt: str | None = "TT",
    rs7412_gt: str | None = "CC",
) -> None:
    """Create a sample DB with APOE SNPs and register it in reference.db.

    A ``None`` genotype omits that SNP row entirely (e.g. ``rs429358_gt=None``
    models a chip that does not assay the ε4-defining SNP — the #806 case).
    """
    # Create sample DB
    sample_db_path = tmp_data_dir / "samples" / "sample_1.db"
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)

    # Insert APOE SNPs (omit any whose genotype is None — models an un-assayed SNP)
    rows = []
    if rs429358_gt is not None:
        rows.append({"rsid": "rs429358", "chrom": "19", "pos": 44908684, "genotype": rs429358_gt})
    if rs7412_gt is not None:
        rows.append({"rsid": "rs7412", "chrom": "19", "pos": 44908822, "genotype": rs7412_gt})
    if rows:
        with sample_engine.begin() as conn:
            conn.execute(sa.insert(raw_variants), rows)

    sample_engine.dispose()

    # Register sample in reference.db
    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)
    with ref_engine.begin() as conn:
        conn.execute(
            sa.insert(samples).values(
                id=1,
                name="test_sample",
                db_path="samples/sample_1.db",
                file_hash="abc123",
            )
        )
    ref_engine.dispose()


@pytest.fixture()
def apoe_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    """FastAPI test client with a sample containing ε3/ε3 (TT + CC)."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
    _create_sample_with_apoe(tmp_data_dir, settings)

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


@pytest.fixture()
def apoe_e4_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    """FastAPI test client with a sample containing ε3/ε4 (CT + CC)."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
    _create_sample_with_apoe(tmp_data_dir, settings, rs429358_gt="CT", rs7412_gt="CC")

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


@contextmanager
def _apoe_client_for(
    tmp_data_dir: Path, rs429358_gt: str | None, rs7412_gt: str | None
) -> Generator[TestClient, None, None]:
    """A TestClient over a sample seeded with the given APOE genotypes (#806).

    Lets a test exercise the un-callable APOE statuses (missing SNP / no-call /
    ambiguous) that the gated /genotype route must surface distinctly from
    ``not_run``.
    """
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
    _create_sample_with_apoe(tmp_data_dir, settings, rs429358_gt=rs429358_gt, rs7412_gt=rs7412_gt)
    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()
        from backend.main import create_app

        with TestClient(create_app()) as tc:
            yield tc
        reset_registry()


# ═══════════════════════════════════════════════════════════════════════
# GET /api/analysis/apoe/disclaimer
# ═══════════════════════════════════════════════════════════════════════


class TestAPOEDisclaimer:
    """Tests for the APOE gate disclosure text endpoint."""

    def test_returns_gate_text(self, apoe_client: TestClient) -> None:
        """Should return the full APOE gate disclosure."""
        resp = apoe_client.get("/api/analysis/apoe/disclaimer")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == APOE_GATE_TITLE
        assert data["text"] == APOE_GATE_TEXT
        assert data["accept_label"] == APOE_GATE_ACCEPT_LABEL
        assert data["decline_label"] == APOE_GATE_DECLINE_LABEL

    def test_text_is_substantial(self, apoe_client: TestClient) -> None:
        """Gate text should be substantial and cover key topics."""
        resp = apoe_client.get("/api/analysis/apoe/disclaimer")
        text = resp.json()["text"]
        assert len(text) > 500

    def test_covers_e4_risk(self, apoe_client: TestClient) -> None:
        """Should explain what ε4 means."""
        resp = apoe_client.get("/api/analysis/apoe/disclaimer")
        text = resp.json()["text"].lower()
        assert "e4" in text
        assert "alzheimer" in text

    def test_not_a_diagnosis(self, apoe_client: TestClient) -> None:
        """Should clarify ε4 is not diagnostic."""
        resp = apoe_client.get("/api/analysis/apoe/disclaimer")
        text = resp.json()["text"].lower()
        assert "does not mean" in text or "not mean" in text

    def test_emotional_distress_warning(self, apoe_client: TestClient) -> None:
        """Should warn about emotional distress."""
        resp = apoe_client.get("/api/analysis/apoe/disclaimer")
        text = resp.json()["text"].lower()
        assert "emotional" in text

    def test_resource_links(self, apoe_client: TestClient) -> None:
        """Should include NIA, Alz Assoc, NSGC links."""
        resp = apoe_client.get("/api/analysis/apoe/disclaimer")
        text = resp.json()["text"]
        assert "nia.nih.gov" in text
        assert "alz.org" in text
        assert "nsgc.org" in text or "findageneticcounselor" in text

    def test_non_dismissible_statement(self, apoe_client: TestClient) -> None:
        """Should state the gate cannot be dismissed."""
        resp = apoe_client.get("/api/analysis/apoe/disclaimer")
        text = resp.json()["text"].lower()
        assert "cannot be dismissed" in text


# ═══════════════════════════════════════════════════════════════════════
# GET /api/analysis/apoe/gate-status
# ═══════════════════════════════════════════════════════════════════════


class TestAPOEGateStatus:
    """Tests for the APOE gate status check endpoint."""

    def test_default_not_acknowledged(self, apoe_client: TestClient) -> None:
        """Gate should not be acknowledged by default."""
        resp = apoe_client.get("/api/analysis/apoe/gate-status", params={"sample_id": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["acknowledged"] is False
        assert data["acknowledged_at"] is None

    def test_acknowledged_after_acknowledge(self, apoe_client: TestClient) -> None:
        """Gate should be acknowledged after POST acknowledge-gate."""
        apoe_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1})
        resp = apoe_client.get("/api/analysis/apoe/gate-status", params={"sample_id": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["acknowledged"] is True
        assert data["acknowledged_at"] is not None

    def test_invalid_sample_returns_404(self, apoe_client: TestClient) -> None:
        """Non-existent sample should return 404."""
        resp = apoe_client.get("/api/analysis/apoe/gate-status", params={"sample_id": 999})
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# POST /api/analysis/apoe/acknowledge-gate
# ═══════════════════════════════════════════════════════════════════════


class TestAPOEGateAcknowledge:
    """Tests for the APOE gate acknowledgment endpoint."""

    def test_acknowledge_gate(self, apoe_client: TestClient) -> None:
        """Should acknowledge the gate and return timestamp."""
        resp = apoe_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["acknowledged"] is True
        assert data["acknowledged_at"] is not None

    def test_idempotent_acknowledge(self, apoe_client: TestClient) -> None:
        """Acknowledging twice should succeed without error."""
        resp1 = apoe_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1})
        resp2 = apoe_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1})
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp2.json()["acknowledged"] is True

    def test_acknowledge_persists(self, apoe_client: TestClient) -> None:
        """Acknowledgment should persist across requests."""
        apoe_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1})
        resp = apoe_client.get("/api/analysis/apoe/gate-status", params={"sample_id": 1})
        assert resp.json()["acknowledged"] is True

    def test_invalid_sample_returns_404(self, apoe_client: TestClient) -> None:
        """Non-existent sample should return 404."""
        resp = apoe_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 999})
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# GET /api/analysis/apoe/genotype
# ═══════════════════════════════════════════════════════════════════════


class TestAPOEGenotype:
    """Tests for the APOE genotype endpoint (ε4 fields gate-protected, issue #46)."""

    # Every sensitive field that must NOT leak before gate acknowledgment.
    _SENSITIVE_FIELDS = (
        "diplotype",
        "has_e4",
        "e4_count",
        "has_e2",
        "e2_count",
        "rs429358_genotype",
        "rs7412_genotype",
    )

    def test_not_run_before_analysis(self, apoe_client: TestClient) -> None:
        """A determinable-but-unanalyzed genotype reports not_run.

        The seeded sample is ε3/ε3 (rs429358 TT + rs7412 CC) — a *determinable*
        genotype — but no APOE finding has been stored, so the route reports
        not_run (reserved for the genuinely un-run/unpersisted case, #806).
        """
        resp = apoe_client.get("/api/analysis/apoe/genotype", params={"sample_id": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "not_run"
        assert data["diplotype"] is None

    # #806: an analysis that ran but is un-callable must NOT collapse to not_run.
    # These statuses carry no ε4 information, so the gated route surfaces them
    # directly — lighting up the previously-dead missing_snps/no_call/ambiguous
    # frontend branches. Each fails on the pre-fix route (all returned not_run).
    def test_missing_rs429358_reports_missing_snps(self, tmp_data_dir: Path) -> None:
        # A real build-36/older chip case: rs7412 typed, the ε4-defining
        # rs429358 absent (the pgp_v3_build36_id300 sample from the issue).
        with _apoe_client_for(tmp_data_dir, rs429358_gt=None, rs7412_gt="CC") as client:
            data = client.get("/api/analysis/apoe/genotype", params={"sample_id": 1}).json()
            assert data["status"] == "missing_snps"
            assert data["diplotype"] is None

    def test_no_call_rs429358_reports_no_call(self, tmp_data_dir: Path) -> None:
        with _apoe_client_for(tmp_data_dir, rs429358_gt="--", rs7412_gt="CC") as client:
            data = client.get("/api/analysis/apoe/genotype", params={"sample_id": 1}).json()
            assert data["status"] == "no_call"

    def test_ambiguous_genotype_reports_ambiguous(self, tmp_data_dir: Path) -> None:
        # rs429358 CC + rs7412 CT is the ε1/ε4 ambiguity.
        with _apoe_client_for(tmp_data_dir, rs429358_gt="CC", rs7412_gt="CT") as client:
            data = client.get("/api/analysis/apoe/genotype", params={"sample_id": 1}).json()
            assert data["status"] == "ambiguous"

    def test_uncallable_status_needs_no_gate_acknowledgment(self, tmp_data_dir: Path) -> None:
        # missing/no-call/ambiguous carry no ε4 info → surfaced WITHOUT acking the
        # gate (no determined_but_locked), unlike a determined genotype.
        with _apoe_client_for(tmp_data_dir, rs429358_gt=None, rs7412_gt="CC") as client:
            data = client.get("/api/analysis/apoe/genotype", params={"sample_id": 1}).json()
            assert data["status"] == "missing_snps"
            assert data["has_e4"] is None and data["e4_count"] is None

    def test_genotype_locked_before_acknowledgment(self, apoe_client: TestClient) -> None:
        """A determined genotype must be locked (no ε4 fields) until the gate is acked."""
        apoe_client.post("/api/analysis/apoe/run", params={"sample_id": 1})
        # Gate NOT acknowledged
        resp = apoe_client.get("/api/analysis/apoe/genotype", params={"sample_id": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "determined_but_locked"
        for field in self._SENSITIVE_FIELDS:
            assert data[field] is None, f"{field} leaked before gate acknowledgment"

    def test_e4_status_hidden_before_acknowledgment(self, apoe_e4_client: TestClient) -> None:
        """For an actual ε4 carrier, ε4 status must not be readable before the gate.

        This is the core defect (#46): the user must be able to choose whether to
        learn their Alzheimer-risk ε4 status, so it cannot be exposed pre-gate.
        """
        apoe_e4_client.post("/api/analysis/apoe/run", params={"sample_id": 1})
        resp = apoe_e4_client.get("/api/analysis/apoe/genotype", params={"sample_id": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "determined_but_locked"
        for field in self._SENSITIVE_FIELDS:
            assert data[field] is None, f"{field} leaked ε4 status before acknowledgment"

    def test_returns_genotype_after_acknowledgment(self, apoe_client: TestClient) -> None:
        """Full genotype is returned once the gate is acknowledged."""
        apoe_client.post("/api/analysis/apoe/run", params={"sample_id": 1})
        apoe_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1})
        resp = apoe_client.get("/api/analysis/apoe/genotype", params={"sample_id": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "determined"
        assert data["diplotype"] == "ε3/ε3"
        assert data["has_e4"] is False
        assert data["e4_count"] == 0

    def test_e4_carrier_genotype_after_acknowledgment(self, apoe_e4_client: TestClient) -> None:
        """ε4 carrier status is reported correctly once the gate is acknowledged."""
        apoe_e4_client.post("/api/analysis/apoe/run", params={"sample_id": 1})
        apoe_e4_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1})
        resp = apoe_e4_client.get("/api/analysis/apoe/genotype", params={"sample_id": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["diplotype"] == "ε3/ε4"
        assert data["has_e4"] is True
        assert data["e4_count"] == 1


# ═══════════════════════════════════════════════════════════════════════
# GET /api/analysis/apoe/findings (gate-protected)
# ═══════════════════════════════════════════════════════════════════════


class TestAPOEFindings:
    """Tests for the APOE findings endpoint (gate-protected)."""

    def test_findings_blocked_before_acknowledgment(self, apoe_client: TestClient) -> None:
        """Findings should return 403 when gate is not acknowledged (T3-20)."""
        apoe_client.post("/api/analysis/apoe/run", params={"sample_id": 1})
        resp = apoe_client.get("/api/analysis/apoe/findings", params={"sample_id": 1})
        assert resp.status_code == 403
        assert "gate" in resp.json()["detail"].lower()

    def test_findings_accessible_after_acknowledgment(self, apoe_client: TestClient) -> None:
        """Findings should be accessible after gate acknowledgment."""
        apoe_client.post("/api/analysis/apoe/run", params={"sample_id": 1})
        apoe_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1})
        resp = apoe_client.get("/api/analysis/apoe/findings", params={"sample_id": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3

    def test_findings_contain_three_categories(self, apoe_client: TestClient) -> None:
        """Findings should contain CV, Alzheimer's, and lipid/dietary."""
        apoe_client.post("/api/analysis/apoe/run", params={"sample_id": 1})
        apoe_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1})
        resp = apoe_client.get("/api/analysis/apoe/findings", params={"sample_id": 1})
        categories = {item["category"] for item in resp.json()["items"]}
        assert "cardiovascular_risk" in categories
        assert "alzheimers_risk" in categories
        assert "lipid_dietary" in categories

    def test_findings_have_evidence_levels(self, apoe_client: TestClient) -> None:
        """Findings should have correct evidence levels."""
        apoe_client.post("/api/analysis/apoe/run", params={"sample_id": 1})
        apoe_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1})
        resp = apoe_client.get("/api/analysis/apoe/findings", params={"sample_id": 1})
        items = resp.json()["items"]
        by_cat = {item["category"]: item for item in items}
        assert by_cat["cardiovascular_risk"]["evidence_level"] == 4
        assert by_cat["alzheimers_risk"]["evidence_level"] == 4
        assert by_cat["lipid_dietary"]["evidence_level"] == 3

    def test_findings_have_pmid_citations(self, apoe_client: TestClient) -> None:
        """All findings should have PubMed citations."""
        apoe_client.post("/api/analysis/apoe/run", params={"sample_id": 1})
        apoe_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1})
        resp = apoe_client.get("/api/analysis/apoe/findings", params={"sample_id": 1})
        for item in resp.json()["items"]:
            assert len(item["pmid_citations"]) > 0

    def test_findings_have_diplotype(self, apoe_client: TestClient) -> None:
        """All findings should carry the diplotype."""
        apoe_client.post("/api/analysis/apoe/run", params={"sample_id": 1})
        apoe_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1})
        resp = apoe_client.get("/api/analysis/apoe/findings", params={"sample_id": 1})
        for item in resp.json()["items"]:
            assert item["diplotype"] == "ε3/ε3"

    def test_e4_findings_blocked_without_gate(self, apoe_e4_client: TestClient) -> None:
        """ε4 carrier findings must be blocked without gate acknowledgment."""
        apoe_e4_client.post("/api/analysis/apoe/run", params={"sample_id": 1})
        resp = apoe_e4_client.get("/api/analysis/apoe/findings", params={"sample_id": 1})
        assert resp.status_code == 403

    def test_e4_findings_accessible_with_gate(self, apoe_e4_client: TestClient) -> None:
        """ε4 carrier findings should be accessible after gate acknowledgment."""
        apoe_e4_client.post("/api/analysis/apoe/run", params={"sample_id": 1})
        apoe_e4_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1})
        resp = apoe_e4_client.get("/api/analysis/apoe/findings", params={"sample_id": 1})
        assert resp.status_code == 200
        assert resp.json()["total"] == 3

    def test_empty_findings_when_not_run(self, apoe_client: TestClient) -> None:
        """Should return empty findings list when analysis not run (after gate ack)."""
        apoe_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1})
        resp = apoe_client.get("/api/analysis/apoe/findings", params={"sample_id": 1})
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


# ═══════════════════════════════════════════════════════════════════════
# POST /api/analysis/apoe/run
# ═══════════════════════════════════════════════════════════════════════


class TestAPOERun:
    """Tests for the APOE analysis run endpoint."""

    def test_run_e3_e3(self, apoe_client: TestClient) -> None:
        """Should run APOE analysis for ε3/ε3 sample, withholding diplotype pre-gate."""
        resp = apoe_client.post("/api/analysis/apoe/run", params={"sample_id": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["genotype_stored"] is True
        assert data["findings_count"] == 3
        # Gate not acknowledged → diplotype withheld (issue #111).
        assert data["diplotype"] is None

    def test_run_e3_e4(self, apoe_e4_client: TestClient) -> None:
        """Should run APOE analysis for ε3/ε4 sample, withholding diplotype pre-gate."""
        resp = apoe_e4_client.post("/api/analysis/apoe/run", params={"sample_id": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["genotype_stored"] is True
        assert data["findings_count"] == 3
        # ε4 status must not leak from the run response before the gate (issue #111).
        assert data["diplotype"] is None

    def test_run_withholds_diplotype_before_gate(self, apoe_e4_client: TestClient) -> None:
        """The ε4-bearing diplotype must not appear anywhere in the pre-gate /run body.

        Core defect (issue #111): the /run response was the same disclosure leak
        #46 fixed for /genotype, via a different vector — it returned the ε4
        diplotype with no gate check.
        """
        resp = apoe_e4_client.post("/api/analysis/apoe/run", params={"sample_id": 1})
        assert resp.status_code == 200
        # No ε4 status leaks via any field of the unacknowledged run response.
        assert "ε4" not in json.dumps(resp.json())

    def test_run_returns_diplotype_after_gate(self, apoe_e4_client: TestClient) -> None:
        """Re-invoking /run after acknowledgment returns the diplotype (issue #111)."""
        apoe_e4_client.post("/api/analysis/apoe/run", params={"sample_id": 1})
        apoe_e4_client.post("/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1})
        resp = apoe_e4_client.post("/api/analysis/apoe/run", params={"sample_id": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["genotype_stored"] is True
        assert data["findings_count"] == 3
        assert data["diplotype"] == "ε3/ε4"

    def test_run_does_not_acknowledge_gate(self, apoe_client: TestClient) -> None:
        """Running analysis should NOT acknowledge the gate."""
        apoe_client.post("/api/analysis/apoe/run", params={"sample_id": 1})
        resp = apoe_client.get("/api/analysis/apoe/gate-status", params={"sample_id": 1})
        assert resp.json()["acknowledged"] is False

    def test_run_idempotent(self, apoe_client: TestClient) -> None:
        """Running analysis twice should succeed (idempotent)."""
        resp1 = apoe_client.post("/api/analysis/apoe/run", params={"sample_id": 1})
        resp2 = apoe_client.post("/api/analysis/apoe/run", params={"sample_id": 1})
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp2.json()["findings_count"] == 3

    def test_invalid_sample_returns_404(self, apoe_client: TestClient) -> None:
        """Non-existent sample should return 404."""
        resp = apoe_client.post("/api/analysis/apoe/run", params={"sample_id": 999})
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# Property invariant: findings never leak before gate acknowledgment
# ═══════════════════════════════════════════════════════════════════════


class TestAPOEGateInvariant:
    """Property-based invariant: APOE findings must not appear before gate ack."""

    def test_findings_never_in_response_before_gate(self, apoe_e4_client: TestClient) -> None:
        """ε4 findings must not leak before gate acknowledgment (T3-20)."""
        # Run analysis
        apoe_e4_client.post("/api/analysis/apoe/run", params={"sample_id": 1})

        # Attempt to get findings WITHOUT gate
        resp = apoe_e4_client.get("/api/analysis/apoe/findings", params={"sample_id": 1})
        assert resp.status_code == 403

        # Genotype endpoint should work but not reveal findings
        genotype_resp = apoe_e4_client.get("/api/analysis/apoe/genotype", params={"sample_id": 1})
        assert genotype_resp.status_code == 200
        # Genotype can show has_e4 but not the clinical implications
        genotype_data = genotype_resp.json()
        assert "finding_text" not in genotype_data
        assert "alzheimer" not in json.dumps(genotype_data).lower()

    def test_gate_then_findings_e2e_flow(self, apoe_e4_client: TestClient) -> None:
        """Full E2E: run → gate blocked → acknowledge → findings visible (F4)."""
        # Step 1: Run analysis — diplotype withheld pre-gate (issue #111)
        run_resp = apoe_e4_client.post("/api/analysis/apoe/run", params={"sample_id": 1})
        assert run_resp.status_code == 200
        assert run_resp.json()["diplotype"] is None

        # Step 2: Findings blocked
        findings_resp = apoe_e4_client.get("/api/analysis/apoe/findings", params={"sample_id": 1})
        assert findings_resp.status_code == 403

        # Step 3: Acknowledge gate
        ack_resp = apoe_e4_client.post(
            "/api/analysis/apoe/acknowledge-gate", params={"sample_id": 1}
        )
        assert ack_resp.status_code == 200
        assert ack_resp.json()["acknowledged"] is True

        # Step 4: Diplotype now revealed by /run, findings now visible
        run_resp = apoe_e4_client.post("/api/analysis/apoe/run", params={"sample_id": 1})
        assert run_resp.json()["diplotype"] == "ε3/ε4"
        findings_resp = apoe_e4_client.get("/api/analysis/apoe/findings", params={"sample_id": 1})
        assert findings_resp.status_code == 200
        data = findings_resp.json()
        assert data["total"] == 3

        # Verify Alzheimer's finding has proper caveats
        alz = next(i for i in data["items"] if i["category"] == "alzheimers_risk")
        assert "probabilistic" in alz["finding_text"].lower()
        assert alz["evidence_level"] == 4
