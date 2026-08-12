"""Tests for admin panel API routes (P4-21b).

Covers:
  - GET /api/admin/logs       — Paginated log explorer with filtering
  - GET /api/admin/db-stats   — Database file stats and row counts
  - GET /api/admin/sample-stats — Sample database stats
  - GET /api/admin/disk-usage — Disk usage breakdown
  - GET /api/admin/status     — System status, uptime, active jobs

Integration test T4-22d: Admin panel log explorer returns paginated log
entries filterable by level and component.
"""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.tables import (
    LOG_ENTRY_PRESENTATION_POLICY_VERSION,
    jobs,
    log_entries,
    reference_metadata,
    samples,
)

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_data_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "samples").mkdir()
    (data_dir / "logs").mkdir()
    return data_dir


@pytest.fixture()
def admin_client(tmp_data_dir: Path) -> Generator[TestClient, None, None]:
    """Create a test client with seeded log entries and sample data."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
    ref_path = settings.reference_db_path
    engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(engine)

    # Seed log entries
    now = datetime.now(UTC)
    log_data = [
        {
            "timestamp": now,
            "level": "INFO",
            "logger": "backend.main",
            "message": "Application started",
            "event_data": '{"host": "127.0.0.1"}',
        },
        {
            "timestamp": now,
            "level": "WARNING",
            "logger": "backend.db.update_manager",
            "message": "ClinVar update check failed",
            "event_data": None,
        },
        {
            "timestamp": now,
            "level": "ERROR",
            "logger": "backend.annotation.engine",
            "message": "Annotation batch failed",
            "event_data": '{"batch_size": 1000}',
        },
        {
            "timestamp": now,
            "level": "INFO",
            "logger": "backend.api.routes.setup",
            "message": "Setup wizard completed",
            "event_data": None,
        },
        {
            "timestamp": now,
            "level": "DEBUG",
            "logger": "backend.db.connection",
            "message": "Engine created for sample_1",
            "event_data": None,
        },
    ]
    with engine.begin() as conn:
        conn.execute(
            sa.insert(log_entries).values(
                presentation_policy_version=LOG_ENTRY_PRESENTATION_POLICY_VERSION
            ),
            log_data,
        )

    # Seed a sample
    with engine.begin() as conn:
        conn.execute(
            sa.insert(samples),
            [
                {
                    "id": 1,
                    "name": "Test Sample",
                    "file_format": "23andme_v5",
                    "file_hash": "abc123",
                    "db_path": "samples/sample_1.db",
                }
            ],
        )

    engine.dispose()

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
        patch("backend.api.routes.admin.get_settings", return_value=settings),
    ):
        reset_registry()
        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            # Seed the active job AFTER lifespan startup so the
            # recover_orphaned_jobs sweep doesn't flip it to "failed".
            post_engine = sa.create_engine(f"sqlite:///{ref_path}")
            with post_engine.begin() as conn:
                conn.execute(
                    sa.insert(jobs),
                    [
                        {
                            "job_id": "test-job-1",
                            "sample_id": 1,
                            "job_type": "annotation",
                            "status": "running",
                            "progress_pct": 45.0,
                            "message": "Annotating variants",
                        }
                    ],
                )
            post_engine.dispose()
            yield tc
        reset_registry()


# ═══════════════════════════════════════════════════════════════════════
# GET /api/admin/logs
# ═══════════════════════════════════════════════════════════════════════


class TestLogExplorer:
    def test_returns_paginated_logs(self, admin_client: TestClient) -> None:
        """GET /api/admin/logs returns paginated log entries."""
        resp = admin_client.get("/api/admin/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data
        assert "has_more" in data
        assert data["total"] == 5
        assert len(data["entries"]) == 5

    def test_filter_by_level(self, admin_client: TestClient) -> None:
        """Filtering by level returns only matching entries."""
        resp = admin_client.get("/api/admin/logs", params={"level": "ERROR"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["entries"][0]["level"] == "ERROR"
        assert "batch failed" in data["entries"][0]["message"]

    def test_filter_by_component(self, admin_client: TestClient) -> None:
        """Filtering by component performs substring match on logger."""
        resp = admin_client.get("/api/admin/logs", params={"component": "update_manager"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert "update_manager" in data["entries"][0]["logger"]

    def test_search_in_message(self, admin_client: TestClient) -> None:
        """Search filter matches within message text."""
        resp = admin_client.get("/api/admin/logs", params={"search": "wizard"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert "wizard" in data["entries"][0]["message"].lower()

    def test_pagination(self, admin_client: TestClient) -> None:
        """Pagination returns correct page slices."""
        resp = admin_client.get("/api/admin/logs", params={"page": 1, "page_size": 2})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["entries"]) == 2
        assert data["has_more"] is True
        assert data["page"] == 1

        resp2 = admin_client.get("/api/admin/logs", params={"page": 3, "page_size": 2})
        data2 = resp2.json()
        assert len(data2["entries"]) == 1
        assert data2["has_more"] is False

        # Full final page: offset + page_size == total (the 5 seeded logs shown
        # exactly, nothing beyond) must NOT advertise a next page (#1229). The
        # cases above probe only 0+2=2 and 4+2=6 vs total 5 — never the == total
        # boundary — so a `<` → `<=` off-by-one (a phantom "Next" page) ships
        # green. 0 + 5 == 5 is False under `<` but True under `<=`.
        resp_full = admin_client.get("/api/admin/logs", params={"page": 1, "page_size": 5})
        data_full = resp_full.json()
        assert len(data_full["entries"]) == 5
        assert data_full["total"] == 5
        assert data_full["has_more"] is False

    def test_newest_first_ordering(self, admin_client: TestClient) -> None:
        """Logs are returned in newest-first order (descending ID)."""
        resp = admin_client.get("/api/admin/logs")
        data = resp.json()
        ids = [e["id"] for e in data["entries"]]
        assert ids == sorted(ids, reverse=True)

    def test_withheld_pgx_logs_are_hidden_before_count_and_pagination(
        self, admin_client: TestClient
    ) -> None:
        """#2019: historical detailed PGx logs must not reopen the guidance hold."""
        from backend.db.connection import get_registry

        with get_registry().reference_engine.begin() as conn:
            conn.execute(
                sa.insert(log_entries).values(presentation_policy_version=0),
                [
                    {
                        "timestamp": datetime.now(UTC),
                        "level": "INFO",
                        "logger": "backend.analysis.pharmacogenomics",
                        "message": "pgx_prescribing_alert",
                        "event_data": json.dumps(
                            {
                                "gene": " CYP2D6 ",
                                "drug": "\ttamoxifen\n",
                                "recommendation": "Escalate tamoxifen to 40 mg/day.",
                            }
                        ),
                    },
                    {
                        "timestamp": datetime.now(UTC),
                        "level": "WARNING",
                        "logger": "backend.analysis.pharmacogenomics",
                        "message": "pgx_prescribing_alert",
                        "event_data": "{malformed legacy prescribing payload",
                    },
                    {
                        "timestamp": datetime.now(UTC),
                        "level": "WARNING",
                        "logger": "backend.analysis.pharmacogenomics",
                        "message": "pgx_prescribing_alert",
                        "event_data": json.dumps(
                            {
                                "gene": 1,
                                "drug": "tamoxifen",
                                "recommendation": "Numeric legacy payload must not render.",
                            }
                        ),
                    },
                    {
                        "timestamp": datetime.now(UTC),
                        "level": "WARNING",
                        "logger": "backend.analysis.pharmacogenomics",
                        "message": "pgx_prescribing_alert",
                        "event_data": json.dumps(
                            {
                                "gene": "CYP2D6",
                                "drug": [],
                                "recommendation": "Array legacy payload must not render.",
                            }
                        ),
                    },
                    {
                        "timestamp": datetime.now(UTC),
                        "level": "WARNING",
                        "logger": "backend.analysis.pharmacogenomics",
                        "message": "pgx_prescribing_alert",
                        "event_data": json.dumps(
                            {
                                "gene": " \t",
                                "drug": "tamoxifen",
                                "recommendation": "Whitespace gene payload must not render.",
                            }
                        ),
                    },
                    {
                        "timestamp": datetime.now(UTC),
                        "level": "WARNING",
                        "logger": "backend.analysis.pharmacogenomics",
                        "message": "pgx_prescribing_alert",
                        "event_data": json.dumps(
                            {
                                "gene": "CYP2D6",
                                "drug": "\n ",
                                "recommendation": "Whitespace drug payload must not render.",
                            }
                        ),
                    },
                    {
                        "timestamp": datetime.now(UTC),
                        "level": "WARNING",
                        "logger": "backend.analysis.pharmacogenomics",
                        "message": "pgx_prescribing_alert",
                        "event_data": (
                            '{"gene":"CYP2D6","drug":"tamoxifen",'
                            '"gene":"CYP2C19","drug":"clopidogrel",'
                            '"recommendation":"Duplicate legacy payload must not render."}'
                        ),
                    },
                    {
                        "timestamp": datetime.now(UTC),
                        "level": "WARNING",
                        "logger": "backend.analysis.pharmacogenomics",
                        "message": "pgx_prescribing_alert",
                        "event_data": json.dumps(
                            {
                                "gene": "CYP2C19",
                                "drug": "clopidogrel",
                                "nested": {
                                    " gene": "CYP2D6",
                                    " drug": "tamoxifen",
                                    "recommendation": "Aliased nested guidance must not render.",
                                },
                            }
                        ),
                    },
                    {
                        "timestamp": datetime.now(UTC),
                        "level": "WARNING",
                        "logger": "backend.analysis.pharmacogenomics",
                        "message": "legacy_pgx_event",
                        "event_data": json.dumps(
                            {
                                "gene": "CYP2C19",
                                "drug": "clopidogrel",
                                "nested": {
                                    " gene": "CYP2D6",
                                    " drug": "tamoxifen",
                                    "recommendation": (
                                        "Unknown-message alias guidance must not render."
                                    ),
                                },
                            }
                        ),
                    },
                    {
                        "timestamp": datetime.now(UTC),
                        "level": "WARNING",
                        "logger": "backend.analysis.pharmacogenomics",
                        "message": "legacy_pgx_event",
                        "event_data": (
                            '{"gene":"CYP2D6","drug":"tamoxifen",'
                            '"gene":"CYP2C19","drug":"clopidogrel",'
                            '"recommendation":"Unknown-message duplicate guidance must not "'
                            '"render."}'
                        ),
                    },
                    {
                        "timestamp": datetime.now(UTC),
                        "level": "WARNING",
                        "logger": "backend.analysis.pharmacogenomics",
                        "message": "legacy_pgx_event",
                        "event_data": json.dumps(
                            "Scalar CYP2D6 tamoxifen guidance must not render."
                        ),
                    },
                    {
                        "timestamp": datetime.now(UTC),
                        "level": "WARNING",
                        "logger": "backend.analysis.pharmacogenomics",
                        "message": "legacy_pgx_event",
                        "event_data": json.dumps(
                            ["List CYP2D6 tamoxifen guidance must not render."]
                        ),
                    },
                ],
            )

        resp = admin_client.get("/api/admin/logs", params={"page": 1, "page_size": 5})
        assert resp.status_code == 200
        data = resp.json()
        rendered = json.dumps(data).lower()
        assert data["total"] == 5
        assert len(data["entries"]) == 5
        assert data["has_more"] is False
        assert "tamoxifen" not in rendered
        assert "40 mg" not in rendered
        assert "malformed legacy" not in rendered
        assert "numeric legacy" not in rendered
        assert "array legacy" not in rendered
        assert "whitespace gene" not in rendered
        assert "whitespace drug" not in rendered
        assert "duplicate legacy" not in rendered
        assert "aliased nested guidance" not in rendered
        assert "unknown-message alias guidance" not in rendered
        assert "unknown-message duplicate guidance" not in rendered
        assert "scalar cyp2d6 tamoxifen guidance" not in rendered
        assert "list cyp2d6 tamoxifen guidance" not in rendered

    def test_log_gate_aggregates_message_and_event_payload_before_pagination(
        self,
        admin_client: TestClient,
    ) -> None:
        """Split or unstructured log text cannot bypass the held-pair gate."""
        from backend.db.connection import get_registry

        baseline = admin_client.get("/api/admin/logs")
        assert baseline.status_code == 200
        baseline_total = baseline.json()["total"]

        with get_registry().reference_engine.begin() as conn:
            conn.execute(
                sa.insert(log_entries).values(presentation_policy_version=0),
                [
                    {
                        "timestamp": datetime.now(UTC),
                        "level": "WARNING",
                        "logger": "backend.analysis.pharmacogenomics",
                        "message": "CYP2D6",
                        "event_data": json.dumps({"recommendation": "tamoxifen guidance"}),
                    },
                    {
                        "timestamp": datetime.now(UTC),
                        "level": "WARNING",
                        "logger": "backend.analysis.pharmacogenomics",
                        "message": "CYP2D6 tamoxifen guidance without payload",
                        "event_data": None,
                    },
                    {
                        "timestamp": datetime.now(UTC),
                        "level": "WARNING",
                        "logger": "backend.analysis.pharmacogenomics",
                        "message": "legacy_pgx_event",
                        "event_data": '{"score": NaN}',
                    },
                ],
            )

        response = admin_client.get("/api/admin/logs", params={"page": 1, "page_size": 50})

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == baseline_total
        rendered = json.dumps(body).lower()
        assert "tamoxifen" not in rendered
        assert "legacy_pgx_event" not in rendered

    def test_current_policy_log_page_withholds_cross_row_escaped_identifier_pair(
        self, admin_client: TestClient
    ) -> None:
        """The final page gate remains a defense for current split fragments."""
        from backend.db.connection import get_registry

        with get_registry().reference_engine.begin() as conn:
            conn.execute(
                sa.insert(log_entries).values(
                    presentation_policy_version=LOG_ENTRY_PRESENTATION_POLICY_VERSION
                ),
                [
                    {
                        "timestamp": datetime.now(UTC),
                        "level": "INFO",
                        "logger": "backend.analysis.ancestry",
                        "message": "CYP2D6",
                        "event_data": None,
                    },
                    {
                        "timestamp": datetime.now(UTC),
                        "level": "INFO",
                        "logger": "backend.analysis.ancestry",
                        "message": "safe separate record",
                        "event_data": '{"note":"tam\\u006fxifen"}',
                    },
                ],
            )

        response = admin_client.get("/api/admin/logs", params={"page": 1, "page_size": 50})

        assert response.status_code == 200
        assert response.json() == {
            "entries": [],
            "total": 0,
            "page": 1,
            "page_size": 50,
            "has_more": False,
        }

    def test_deeply_nested_pgx_logs_fail_closed(self, admin_client: TestClient) -> None:
        """#2019: deeply nested legacy PGx JSON cannot crash Log Explorer."""
        from backend.db.connection import get_registry

        baseline = admin_client.get("/api/admin/logs")
        assert baseline.status_code == 200
        baseline_total = baseline.json()["total"]

        payload: dict[str, object] = {
            "gene": "CYP2C19",
            "drug": "clopidogrel",
            "recommendation": "Deep legacy payload must not render.",
        }
        nested = payload
        for _ in range(600):
            child: dict[str, object] = {}
            nested["nested"] = child
            nested = child

        with get_registry().reference_engine.begin() as conn:
            conn.execute(
                sa.insert(log_entries).values(
                    timestamp=datetime.now(UTC),
                    level="WARNING",
                    logger="backend.analysis.pharmacogenomics",
                    message="pgx_prescribing_alert",
                    event_data=json.dumps(payload),
                    presentation_policy_version=0,
                )
            )

        response = admin_client.get("/api/admin/logs")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == baseline_total
        assert "deep legacy payload" not in json.dumps(data).lower()

    def test_sql_pagination_excludes_legacy_rows_without_a_scan_threshold(
        self, admin_client: TestClient
    ) -> None:
        """A history beyond the former scan cap remains exact and available."""
        from backend.db.connection import get_registry

        current_row_count = 10_001
        with get_registry().reference_engine.begin() as conn:
            conn.execute(
                sa.insert(log_entries).values(
                    presentation_policy_version=LOG_ENTRY_PRESENTATION_POLICY_VERSION
                ),
                [
                    {
                        "timestamp": datetime.now(UTC),
                        "level": "INFO",
                        "logger": "backend.operations",
                        "message": f"attested operational event {index}",
                        "event_data": None,
                    }
                    for index in range(current_row_count)
                ],
            )

        params = {"page": 21, "page_size": 500}
        response = admin_client.get("/api/admin/logs", params=params)

        assert response.status_code == 200
        expected = response.json()
        assert expected["total"] == current_row_count + 5
        assert len(expected["entries"]) == 6
        assert expected["has_more"] is False

        # This historical detailed row must not change a response's contents,
        # count, or availability simply because it sits beyond an old scan cap.
        with get_registry().reference_engine.begin() as conn:
            conn.execute(
                sa.insert(log_entries).values(
                    timestamp=datetime.now(UTC),
                    level="WARNING",
                    logger="backend.analysis.pharmacogenomics",
                    message="pgx_prescribing_alert",
                    event_data=json.dumps(
                        {
                            "gene": "CYP2D6",
                            "drug": "tamoxifen",
                            "recommendation": "Historic guidance must remain quarantined.",
                        }
                    ),
                    presentation_policy_version=0,
                )
            )

        repeated = admin_client.get("/api/admin/logs", params=params)
        assert repeated.status_code == 200
        assert repeated.json() == expected

    def test_event_data_included(self, admin_client: TestClient) -> None:
        """Entries with event_data have the JSON field populated."""
        resp = admin_client.get("/api/admin/logs", params={"level": "INFO"})
        data = resp.json()
        # At least one INFO entry has event_data
        entries_with_data = [e for e in data["entries"] if e["event_data"]]
        assert len(entries_with_data) >= 1


# ═══════════════════════════════════════════════════════════════════════
# GET /api/admin/db-stats
# ═══════════════════════════════════════════════════════════════════════


class TestDbStats:
    def test_returns_db_stats(self, admin_client: TestClient) -> None:
        """GET /api/admin/db-stats returns stats for all reference databases."""
        resp = admin_client.get("/api/admin/db-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        # At least reference.db should be present
        names = [d["name"] for d in data]
        assert "reference" in names

    def test_reference_db_exists(self, admin_client: TestClient) -> None:
        """Reference DB is reported as existing."""
        resp = admin_client.get("/api/admin/db-stats")
        data = resp.json()
        ref = next(d for d in data if d["name"] == "reference")
        assert ref["exists"] is True
        assert ref["file_size_bytes"] is not None
        assert ref["file_size_bytes"] > 0

    def test_stats_include_display_name(self, admin_client: TestClient) -> None:
        """Each DB stat includes a display_name."""
        resp = admin_client.get("/api/admin/db-stats")
        data = resp.json()
        for db in data:
            assert "display_name" in db
            assert db["display_name"]


def _make_lai_bundle(data_dir: Path) -> None:
    """Create a structurally-valid extracted LAI bundle directory."""
    bundle = data_dir / "lai_bundle"
    for chrom in range(1, 23):
        model_dir = bundle / "gnomix_models" / f"chr{chrom}"
        model_dir.mkdir(parents=True, exist_ok=True)
        for fname in ("base_coefs.npz", "metadata.npz", "smoother.json"):
            (model_dir / fname).write_bytes(b"")
    mapping = bundle / "liftover" / "array_site_mapping.tsv"
    mapping.parent.mkdir(parents=True)
    mapping.write_text("rs1\tchr1\t100\n", encoding="utf-8")


class TestDbStatsPresence:
    """Regression: installed reference-resident / directory DBs report present.

    The previous ``exists = dest_path().exists()`` check looked for a standalone
    ``data_dir/<filename>`` file, which never exists for reference.db-resident
    DBs (clinvar/cpic/clingen, whose data is a table inside reference.db) nor for
    the LAI bundle (the tarball is deleted after extraction), so they rendered as
    "Missing" even when installed. Presence now comes from the readability probe
    the Database Health panel uses.
    """

    def _seed(self, tmp_data_dir: Path) -> None:
        from backend.db.tables import clinvar_variants, cpic_alleles, database_versions

        settings = Settings(data_dir=tmp_data_dir, wal_mode=False)
        engine = sa.create_engine(f"sqlite:///{settings.reference_db_path}")
        with engine.begin() as conn:
            conn.execute(
                sa.insert(clinvar_variants),
                [
                    {"chrom": "1", "pos": 100, "ref": "A", "alt": "G", "rsid": "rs1"},
                    {"chrom": "2", "pos": 200, "ref": "C", "alt": "T", "rsid": "rs2"},
                ],
            )
            conn.execute(
                sa.insert(cpic_alleles),
                [{"gene": "CYP2D6", "allele_name": "*1"}],
            )
            # ClinGen schema exists but is intentionally left empty.
            conn.execute(
                sa.insert(database_versions),
                [
                    {
                        "db_name": "clinvar",
                        "version": "20260301",
                        "file_size_bytes": 30_000_000,
                        "downloaded_at": datetime.now(UTC),
                    },
                ],
            )
        engine.dispose()
        _make_lai_bundle(tmp_data_dir)

    def test_reference_resident_and_directory_dbs_report_present(
        self, admin_client: TestClient, tmp_data_dir: Path
    ) -> None:
        self._seed(tmp_data_dir)

        resp = admin_client.get("/api/admin/db-stats")
        assert resp.status_code == 200
        by_name = {d["name"]: d for d in resp.json()}

        # Reference-resident DB with data → present, counted against reference.db,
        # version + last_updated surfaced from the version stamp.
        clinvar = by_name["clinvar"]
        assert clinvar["exists"] is True
        assert clinvar["row_count"] == 2
        assert clinvar["version"] == "20260301"
        assert clinvar["last_updated"] is not None
        # The reported artifact is reference.db, not a phantom data_dir/clinvar.db.
        assert clinvar["file_path"].endswith("reference.db")

        # A second reference-resident DB with data.
        assert by_name["cpic"]["exists"] is True
        assert by_name["cpic"]["row_count"] == 1

        # The LAI directory bundle (no standalone tarball) is present.
        assert by_name["lai_bundle"]["exists"] is True

    def test_empty_reference_resident_db_reports_missing(
        self, admin_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """No false positive: a reference-resident DB whose table is empty is Missing.

        reference.db exists (schema created), but ClinGen was never loaded — bare
        file presence would wrongly read "Available"; the readability probe must
        report it Missing.
        """
        self._seed(tmp_data_dir)

        resp = admin_client.get("/api/admin/db-stats")
        by_name = {d["name"]: d for d in resp.json()}
        assert by_name["clingen"]["exists"] is False


# ═══════════════════════════════════════════════════════════════════════
# GET /api/admin/sample-stats
# ═══════════════════════════════════════════════════════════════════════


class TestSampleStats:
    def test_returns_sample_stats(self, admin_client: TestClient) -> None:
        """GET /api/admin/sample-stats returns stats for registered samples."""
        resp = admin_client.get("/api/admin/sample-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "Test Sample"
        assert data[0]["sample_id"] == 1


# ═══════════════════════════════════════════════════════════════════════
# GET /api/admin/disk-usage
# ═══════════════════════════════════════════════════════════════════════


class TestDiskUsage:
    def test_returns_disk_usage(self, admin_client: TestClient) -> None:
        """GET /api/admin/disk-usage returns disk space info."""
        resp = admin_client.get("/api/admin/disk-usage")
        assert resp.status_code == 200
        data = resp.json()
        assert "data_dir" in data
        assert "total_bytes" in data
        assert "free_bytes" in data
        assert "used_bytes" in data
        assert "reference_dbs_bytes" in data
        assert "sample_dbs_bytes" in data
        assert "logs_bytes" in data
        assert data["total_bytes"] > 0

    def test_reference_dbs_bytes_non_negative(self, admin_client: TestClient) -> None:
        """Reference DB bytes is non-negative."""
        resp = admin_client.get("/api/admin/disk-usage")
        data = resp.json()
        assert data["reference_dbs_bytes"] >= 0


# ═══════════════════════════════════════════════════════════════════════
# GET /api/admin/status
# ═══════════════════════════════════════════════════════════════════════


class TestSystemStatus:
    def test_returns_status(self, admin_client: TestClient) -> None:
        """GET /api/admin/status returns system status."""
        resp = admin_client.get("/api/admin/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert "uptime_seconds" in data
        assert "data_dir" in data
        assert "active_jobs" in data
        assert "total_samples" in data
        assert "auth_enabled" in data
        assert "log_level" in data

    def test_uptime_positive(self, admin_client: TestClient) -> None:
        """Uptime is a positive number."""
        resp = admin_client.get("/api/admin/status")
        data = resp.json()
        assert data["uptime_seconds"] > 0

    def test_active_jobs_present(self, admin_client: TestClient) -> None:
        """Active jobs include the seeded running job."""
        resp = admin_client.get("/api/admin/status")
        data = resp.json()
        assert len(data["active_jobs"]) == 1
        job = data["active_jobs"][0]
        assert job["job_id"] == "test-job-1"
        assert job["job_type"] == "annotation"
        assert job["status"] == "running"
        assert job["progress_pct"] == 45.0

    def test_active_jobs_excludes_terminal_status(self, admin_client: TestClient) -> None:
        """#808: the Active Jobs panel filters the jobs table to in-flight
        statuses (pending/running/cancelling). Seed terminal-status jobs alongside the
        running one and assert /status returns ONLY the active job.

        The single-running-job fixture could not distinguish "filter to active"
        from "return all jobs"; the "completed/failed id absent" checks here kill
        a dropped or broadened status filter (e.g. removing the ``.where(...)``),
        which would otherwise list finished jobs as if still active.
        """
        # Reach the same reference DB the route reads (registry.reference_engine).
        from backend.db.connection import get_registry

        registry = get_registry()
        with registry.reference_engine.begin() as conn:
            conn.execute(
                sa.insert(jobs),
                [
                    {
                        "job_id": "test-job-cancelling",
                        "sample_id": 1,
                        "job_type": "annotation",
                        "status": "cancelling",
                        "progress_pct": 45.0,
                        "message": "Cancellation requested",
                    },
                    {
                        "job_id": "test-job-completed",
                        "sample_id": 1,
                        "job_type": "annotation",
                        "status": "completed",
                        "progress_pct": 100.0,
                        "message": "Annotation complete",
                    },
                    {
                        "job_id": "test-job-failed",
                        "sample_id": 1,
                        "job_type": "annotation",
                        "status": "failed",
                        "progress_pct": 0.0,
                        "message": "Annotation failed",
                    },
                ],
            )

        resp = admin_client.get("/api/admin/status")
        data = resp.json()
        active_ids = {j["job_id"] for j in data["active_jobs"]}
        # Running and cancellation-acknowledgement jobs are active; terminal
        # statuses remain excluded.
        assert active_ids == {"test-job-1", "test-job-cancelling"}
        assert "test-job-completed" not in active_ids
        assert "test-job-failed" not in active_ids
        assert all(
            j["status"] in {"pending", "running", "cancelling"} for j in data["active_jobs"]
        )

    def test_total_samples(self, admin_client: TestClient) -> None:
        """Total samples count matches seeded data."""
        resp = admin_client.get("/api/admin/status")
        data = resp.json()
        assert data["total_samples"] == 1

    def test_version_format(self, admin_client: TestClient) -> None:
        """Version is a non-empty string."""
        resp = admin_client.get("/api/admin/status")
        data = resp.json()
        assert len(data["version"]) > 0
