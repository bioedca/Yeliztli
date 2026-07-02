"""Tests for the individuals API endpoints (Steps 47, 55, 67; Plan §9.2, §9.3, §10.6, §14.1).

Step 47 / IND-03 covered:
- (a) Happy paths for all seven endpoints (list, create, detail, patch,
       delete, link-sample, unlink-sample).
- (b) 409 on double-link — linking a sample already attached to a
       different individual returns the existing link in the body.
- (c) FK SET NULL on individual delete — DELETE on an individual with
       linked samples nulls out each ``samples.individual_id`` but leaves
       the sample rows + per-sample DB files in place. Plan §9.2.

Step 55 / IND-09a edge-case extensions (``TestEdgeCases``):
- Linking against nonexistent individual / nonexistent sample → 404.
- Unlinking against nonexistent individual / nonexistent sample → 404.
- Aggregated-findings dedup when the same rsid appears in 3 linked
  samples → single emission; ``linked_samples`` carries the provenance.
- Low-evidence findings (evidence_level < 3) are excluded from the
  aggregate.
- Findings with NULL rsid (haplogroup / pathway-level) are counted
  per-sample rather than deduped (Plan §9.5 carve-out).
- 409 link-elsewhere preserves the original attachment across repeated
  relink attempts.

Step 67 / MRG-03 smoke coverage of ``POST /api/individuals/{id}/merge/preview``
(``TestMergePreviewRoute``): 404 on nonexistent individual, 422 on
shape/membership/status failures, FastAPI pydantic validation on the
body shape. The exhaustive surface (full happy-path payload, stale-source
423, missing-state fallback) lives in ``test_sample_merge_preview.py``
which lands in Step 73.

Sex-inference + haplogroup parity lives in step 54; migration 009
round-trip in step 46.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db.connection import reset_registry
from backend.db.sample_schema import create_sample_tables
from backend.db.tables import annotated_variants, apoe_gate, reference_metadata, samples
from backend.db.tables import findings as findings_table

# ── Fixtures ─────────────────────────────────────────────────────────


def _seed_sample(
    ref_engine: sa.Engine,
    data_dir: Path,
    *,
    name: str,
    db_path: str,
    file_format: str,
    file_hash: str,
) -> int:
    """Insert a samples row + create the per-sample DB file. Returns id."""
    with ref_engine.begin() as conn:
        result = conn.execute(
            samples.insert().values(
                name=name,
                db_path=db_path,
                file_format=file_format,
                file_hash=file_hash,
            )
        )
        sample_id = result.inserted_primary_key[0]

    sample_db_path = data_dir / db_path
    sample_db_path.parent.mkdir(parents=True, exist_ok=True)
    sample_engine = sa.create_engine(f"sqlite:///{sample_db_path}")
    create_sample_tables(sample_engine)
    sample_engine.dispose()
    return sample_id


def _seed_high_confidence_findings(
    data_dir: Path,
    db_path: str,
    rows: list[dict],
) -> None:
    sample_engine = sa.create_engine(f"sqlite:///{data_dir / db_path}")
    with sample_engine.begin() as conn:
        conn.execute(annotated_variants.insert(), rows)
        conn.execute(
            findings_table.insert(),
            [
                {
                    "module": "cancer",
                    "evidence_level": row.get("evidence_level", 4),
                    "rsid": row["rsid"],
                    "finding_text": f"{row['rsid']} finding",
                }
                for row in rows
            ],
        )
    sample_engine.dispose()


def _seed_extra_sample(
    data_dir: Path,
    *,
    name: str,
    db_path: str,
    file_format: str,
    file_hash: str,
    findings_rows: list[dict] | None = None,
    findings_payloads: list[dict] | None = None,
) -> int:
    """Seed a sample row + per-sample DB after the fixture has already started.

    ``findings_rows`` is the legacy shape that mirrors the fixture's
    bootstrap (a single annotated_variants row paired with one
    findings_table row at the row's rsid + evidence_level). When a test
    needs to write findings the bootstrap helper can't express — e.g.
    NULL rsid, a custom module, evidence_level=1 — pass
    ``findings_payloads`` directly: each dict is forwarded verbatim to
    ``findings_table.insert()``.
    """
    ref_engine = sa.create_engine(f"sqlite:///{data_dir / 'reference.db'}")
    try:
        sample_id = _seed_sample(
            ref_engine,
            data_dir,
            name=name,
            db_path=db_path,
            file_format=file_format,
            file_hash=file_hash,
        )
    finally:
        ref_engine.dispose()

    if findings_rows:
        _seed_high_confidence_findings(data_dir, db_path, findings_rows)

    if findings_payloads:
        sample_engine = sa.create_engine(f"sqlite:///{data_dir / db_path}")
        with sample_engine.begin() as conn:
            conn.execute(findings_table.insert(), findings_payloads)
        sample_engine.dispose()

    return sample_id


@pytest.fixture
def individuals_client(tmp_data_dir: Path) -> TestClient:
    """FastAPI TestClient with the reference DB initialized + two samples seeded."""
    settings = Settings(data_dir=tmp_data_dir, wal_mode=False)

    ref_path = settings.reference_db_path
    ref_engine = sa.create_engine(f"sqlite:///{ref_path}")
    reference_metadata.create_all(ref_engine)

    sample1_id = _seed_sample(
        ref_engine,
        tmp_data_dir,
        name="23andMe Export",
        db_path="samples/sample_1.db",
        file_format="23andme_v5",
        file_hash="hash_23andme",
    )
    sample2_id = _seed_sample(
        ref_engine,
        tmp_data_dir,
        name="AncestryDNA Export",
        db_path="samples/sample_2.db",
        file_format="ancestrydna_v2.0",
        file_hash="hash_ancestry",
    )

    # Seed each sample DB with a couple of high-confidence findings;
    # rs12345 is shared so the aggregate count dedupes to 3 rsids.
    _seed_high_confidence_findings(
        tmp_data_dir,
        "samples/sample_1.db",
        [
            {"rsid": "rs12345", "chrom": "1", "pos": 100, "evidence_level": 4},
            {"rsid": "rs67890", "chrom": "2", "pos": 200, "evidence_level": 3},
        ],
    )
    _seed_high_confidence_findings(
        tmp_data_dir,
        "samples/sample_2.db",
        [
            {"rsid": "rs12345", "chrom": "1", "pos": 100, "evidence_level": 4},
            {"rsid": "rs99999", "chrom": "3", "pos": 300, "evidence_level": 4},
        ],
    )

    ref_engine.dispose()

    # Stash sample ids on the settings so tests can read them.
    settings.__dict__["_test_sample1_id"] = sample1_id
    settings.__dict__["_test_sample2_id"] = sample2_id

    with (
        patch("backend.main.get_settings", return_value=settings),
        patch("backend.db.connection.get_settings", return_value=settings),
    ):
        reset_registry()

        from backend.main import create_app

        app = create_app()
        with TestClient(app) as tc:
            tc.sample1_id = sample1_id  # type: ignore[attr-defined]
            tc.sample2_id = sample2_id  # type: ignore[attr-defined]
            tc.data_dir = tmp_data_dir  # type: ignore[attr-defined]
            yield tc

        reset_registry()


# ── (a) Happy paths ──────────────────────────────────────────────────


class TestHappyPaths:
    def test_create_then_list_then_get(self, individuals_client: TestClient) -> None:
        # Empty list to start.
        resp = individuals_client.get("/api/individuals")
        assert resp.status_code == 200
        assert resp.json() == []

        # Create.
        resp = individuals_client.post(
            "/api/individuals",
            json={
                "display_name": "Subject A",
                "notes": "primary subject",
                "biological_sex": "XX",
            },
        )
        assert resp.status_code == 201
        created = resp.json()
        assert created["display_name"] == "Subject A"
        assert created["notes"] == "primary subject"
        assert created["biological_sex"] == "XX"
        assert created["linked_samples"] == []
        assert created["aggregated_findings_count"] == 0
        ind_id = created["id"]

        # Get detail.
        resp = individuals_client.get(f"/api/individuals/{ind_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == ind_id
        assert body["display_name"] == "Subject A"

        # List now contains the row with summary fields.
        resp = individuals_client.get("/api/individuals")
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["id"] == ind_id
        assert rows[0]["sample_count"] == 0
        assert rows[0]["vendors"] == []
        assert rows[0]["last_activity"] is None

    def test_patch_updates_fields(self, individuals_client: TestClient) -> None:
        ind_id = individuals_client.post(
            "/api/individuals", json={"display_name": "Original"}
        ).json()["id"]

        resp = individuals_client.patch(
            f"/api/individuals/{ind_id}",
            json={"display_name": "Updated", "biological_sex": "XY"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["display_name"] == "Updated"
        assert body["biological_sex"] == "XY"

        # Notes can be set to a non-empty string after creation.
        resp = individuals_client.patch(
            f"/api/individuals/{ind_id}",
            json={"notes": "follow-up next quarter"},
        )
        assert resp.status_code == 200
        assert resp.json()["notes"] == "follow-up next quarter"

    def test_patch_with_no_fields_returns_422(self, individuals_client: TestClient) -> None:
        ind_id = individuals_client.post(
            "/api/individuals", json={"display_name": "Original"}
        ).json()["id"]
        resp = individuals_client.patch(f"/api/individuals/{ind_id}", json={})
        assert resp.status_code == 422

    def test_patch_explicit_null_display_name_returns_422(
        self, individuals_client: TestClient
    ) -> None:
        ind_id = individuals_client.post(
            "/api/individuals", json={"display_name": "Original"}
        ).json()["id"]

        resp = individuals_client.patch(
            f"/api/individuals/{ind_id}",
            json={"display_name": None},
        )

        assert resp.status_code == 422

        after = individuals_client.get(f"/api/individuals/{ind_id}")
        assert after.status_code == 200
        assert after.json()["display_name"] == "Original"

    def test_link_and_unlink_sample(self, individuals_client: TestClient) -> None:
        sample1_id = individuals_client.sample1_id  # type: ignore[attr-defined]
        sample2_id = individuals_client.sample2_id  # type: ignore[attr-defined]

        ind_id = individuals_client.post(
            "/api/individuals", json={"display_name": "Subject"}
        ).json()["id"]

        # Link sample 1.
        resp = individuals_client.post(
            f"/api/individuals/{ind_id}/link-sample",
            json={"sample_id": sample1_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert [s["id"] for s in body["linked_samples"]] == [sample1_id]
        assert body["linked_samples"][0]["vendor"] == "23andme"
        assert body["linked_samples"][0]["is_merged"] is False

        # Link sample 2 — both now attached, vendors deduped to two entries.
        resp = individuals_client.post(
            f"/api/individuals/{ind_id}/link-sample",
            json={"sample_id": sample2_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert sorted(s["id"] for s in body["linked_samples"]) == sorted([sample1_id, sample2_id])
        # 3 distinct high-confidence rsids across the two samples: rs12345
        # (shared), rs67890, rs99999.
        assert body["aggregated_findings_count"] == 3

        # List summary reflects both vendors.
        rows = individuals_client.get("/api/individuals").json()
        target = next(r for r in rows if r["id"] == ind_id)
        assert target["sample_count"] == 2
        assert sorted(target["vendors"]) == ["23andme", "ancestrydna"]
        assert target["last_activity"] is not None

        # Unlink sample 1.
        resp = individuals_client.post(
            f"/api/individuals/{ind_id}/unlink-sample",
            json={"sample_id": sample1_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert [s["id"] for s in body["linked_samples"]] == [sample2_id]

        # Re-linking the same sample is a no-op.
        resp = individuals_client.post(
            f"/api/individuals/{ind_id}/link-sample",
            json={"sample_id": sample2_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert [s["id"] for s in body["linked_samples"]] == [sample2_id]

    def test_linked_samples_mark_merged_outputs(
        self, individuals_client: TestClient, tmp_data_dir: Path
    ) -> None:
        sample1_id = individuals_client.sample1_id  # type: ignore[attr-defined]
        merged_id = _seed_extra_sample(
            tmp_data_dir,
            name="Subject merged",
            db_path="samples/sample_merged.db",
            file_format="merged_v1",
            file_hash="hash_merged",
        )

        ind_id = individuals_client.post(
            "/api/individuals", json={"display_name": "Subject"}
        ).json()["id"]
        for sid in (sample1_id, merged_id):
            resp = individuals_client.post(
                f"/api/individuals/{ind_id}/link-sample",
                json={"sample_id": sid},
            )
            assert resp.status_code == 200

        body = individuals_client.get(f"/api/individuals/{ind_id}").json()
        merged_flags = {sample["id"]: sample["is_merged"] for sample in body["linked_samples"]}
        assert merged_flags[sample1_id] is False
        assert merged_flags[merged_id] is True

    def test_create_minimal_body(self, individuals_client: TestClient) -> None:
        resp = individuals_client.post("/api/individuals", json={"display_name": "Minimal"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["notes"] is None
        assert body["biological_sex"] is None

    def test_create_rejects_empty_display_name(self, individuals_client: TestClient) -> None:
        resp = individuals_client.post("/api/individuals", json={"display_name": ""})
        assert resp.status_code == 422

    def test_get_nonexistent_returns_404(self, individuals_client: TestClient) -> None:
        resp = individuals_client.get("/api/individuals/999")
        assert resp.status_code == 404

    def test_patch_nonexistent_returns_404(self, individuals_client: TestClient) -> None:
        resp = individuals_client.patch("/api/individuals/999", json={"display_name": "Foo"})
        assert resp.status_code == 404

    def test_delete_nonexistent_returns_404(self, individuals_client: TestClient) -> None:
        resp = individuals_client.delete("/api/individuals/999")
        assert resp.status_code == 404


# ── (b) 409 on double-link ───────────────────────────────────────────


class TestDoubleLinkConflict:
    def test_409_returns_existing_link_in_body(self, individuals_client: TestClient) -> None:
        sample1_id = individuals_client.sample1_id  # type: ignore[attr-defined]

        ind_a = individuals_client.post("/api/individuals", json={"display_name": "Owner"}).json()
        ind_b = individuals_client.post("/api/individuals", json={"display_name": "Other"}).json()

        # Attach to A first.
        resp = individuals_client.post(
            f"/api/individuals/{ind_a['id']}/link-sample",
            json={"sample_id": sample1_id},
        )
        assert resp.status_code == 200

        # Attempt to attach the same sample to B → 409 with payload.
        resp = individuals_client.post(
            f"/api/individuals/{ind_b['id']}/link-sample",
            json={"sample_id": sample1_id},
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["sample_id"] == sample1_id
        assert detail["individual_id"] == ind_a["id"]
        assert detail["individual_display_name"] == "Owner"
        assert str(ind_a["id"]) in detail["message"]

        # The original link must be untouched.
        resp = individuals_client.get(f"/api/individuals/{ind_a['id']}")
        assert [s["id"] for s in resp.json()["linked_samples"]] == [sample1_id]


# ── (c) FK SET NULL on individual delete ─────────────────────────────


class TestDeleteCascadesToSetNull:
    def test_delete_individual_nulls_linked_samples_without_deleting(
        self, individuals_client: TestClient, tmp_data_dir: Path
    ) -> None:
        sample1_id = individuals_client.sample1_id  # type: ignore[attr-defined]
        sample2_id = individuals_client.sample2_id  # type: ignore[attr-defined]

        # Link both samples to one individual.
        ind = individuals_client.post("/api/individuals", json={"display_name": "Subject"}).json()
        for sid in (sample1_id, sample2_id):
            individuals_client.post(
                f"/api/individuals/{ind['id']}/link-sample",
                json={"sample_id": sid},
            )

        # Per-sample DB files must exist before delete.
        sample1_db = tmp_data_dir / "samples/sample_1.db"
        sample2_db = tmp_data_dir / "samples/sample_2.db"
        assert sample1_db.is_file()
        assert sample2_db.is_file()

        # DELETE the individual.
        resp = individuals_client.delete(f"/api/individuals/{ind['id']}")
        assert resp.status_code == 204

        # Individual is gone.
        resp = individuals_client.get(f"/api/individuals/{ind['id']}")
        assert resp.status_code == 404

        # Both sample rows survive with individual_id == NULL.
        for sid in (sample1_id, sample2_id):
            resp = individuals_client.get(f"/api/samples/{sid}")
            assert resp.status_code == 200

        # Inspect the reference DB directly so the assertion doesn't
        # depend on /api/samples projecting the individual_id field.
        ref_engine = sa.create_engine(f"sqlite:///{tmp_data_dir / 'reference.db'}")
        try:
            with ref_engine.connect() as conn:
                rows = conn.execute(
                    sa.select(samples.c.id, samples.c.individual_id).where(
                        samples.c.id.in_([sample1_id, sample2_id])
                    )
                ).fetchall()
        finally:
            ref_engine.dispose()
        assert {row.id for row in rows} == {sample1_id, sample2_id}
        assert all(row.individual_id is None for row in rows)

        # Per-sample DB files are untouched.
        assert sample1_db.is_file()
        assert sample2_db.is_file()


# ── (d) IND-09a edge-case route tests (Step 55) ──────────────────────


class TestEdgeCases:
    """IND-09a — link/unlink 404 paths + aggregated-findings dedup edges.

    Plan §14.1 IND-09a row. The 409-on-double-link payload assertion
    landed in step 47 (``TestDoubleLinkConflict``); this class extends it
    with multi-attempt preservation and exercises the remaining 404
    branches (link / unlink against missing individual or sample id) +
    three aggregated-findings dedup edges (3-way same-rsid dedup,
    low-evidence exclusion, NULL-rsid per-sample counting).
    """

    # ── Link / unlink 404 paths ────────────────────────────────────

    def test_link_sample_to_nonexistent_individual_returns_404(
        self, individuals_client: TestClient
    ) -> None:
        sample1_id = individuals_client.sample1_id  # type: ignore[attr-defined]
        resp = individuals_client.post(
            "/api/individuals/9999/link-sample",
            json={"sample_id": sample1_id},
        )
        assert resp.status_code == 404
        assert "9999" in resp.json()["detail"]

        # The sample must remain unlinked — the 404 is raised before any
        # write to ``samples.individual_id``.
        ref_engine = sa.create_engine(
            f"sqlite:///{individuals_client.data_dir / 'reference.db'}"  # type: ignore[attr-defined]
        )
        try:
            with ref_engine.connect() as conn:
                row = conn.execute(
                    sa.select(samples.c.individual_id).where(samples.c.id == sample1_id)
                ).fetchone()
        finally:
            ref_engine.dispose()
        assert row is not None
        assert row.individual_id is None

    def test_link_nonexistent_sample_to_existing_individual_returns_404(
        self, individuals_client: TestClient
    ) -> None:
        ind = individuals_client.post("/api/individuals", json={"display_name": "Holder"}).json()
        resp = individuals_client.post(
            f"/api/individuals/{ind['id']}/link-sample",
            json={"sample_id": 9999},
        )
        assert resp.status_code == 404
        assert "9999" in resp.json()["detail"]

        # The individual's linked_samples must still be empty.
        detail = individuals_client.get(f"/api/individuals/{ind['id']}").json()
        assert detail["linked_samples"] == []

    def test_unlink_sample_from_nonexistent_individual_returns_404(
        self, individuals_client: TestClient
    ) -> None:
        sample1_id = individuals_client.sample1_id  # type: ignore[attr-defined]
        resp = individuals_client.post(
            "/api/individuals/9999/unlink-sample",
            json={"sample_id": sample1_id},
        )
        assert resp.status_code == 404
        assert "9999" in resp.json()["detail"]

    def test_unlink_nonexistent_sample_from_existing_individual_returns_404(
        self, individuals_client: TestClient
    ) -> None:
        ind = individuals_client.post("/api/individuals", json={"display_name": "Holder"}).json()
        resp = individuals_client.post(
            f"/api/individuals/{ind['id']}/unlink-sample",
            json={"sample_id": 9999},
        )
        assert resp.status_code == 404
        assert "9999" in resp.json()["detail"]

    # ── 409 link-elsewhere — preservation across repeated attempts ─

    def test_409_link_elsewhere_preserves_original_after_multiple_attempts(
        self, individuals_client: TestClient
    ) -> None:
        """Repeated relink attempts return the same 409 payload and never
        mutate the original ``samples.individual_id``. Extends step 47's
        single-shot assertion.
        """
        sample1_id = individuals_client.sample1_id  # type: ignore[attr-defined]

        owner = individuals_client.post("/api/individuals", json={"display_name": "Owner"}).json()
        other_a = individuals_client.post(
            "/api/individuals", json={"display_name": "Other A"}
        ).json()
        other_b = individuals_client.post(
            "/api/individuals", json={"display_name": "Other B"}
        ).json()

        # Attach to Owner first.
        individuals_client.post(
            f"/api/individuals/{owner['id']}/link-sample",
            json={"sample_id": sample1_id},
        )

        # Two distinct relink attempts each return 409 pointing at Owner.
        for other in (other_a, other_b):
            resp = individuals_client.post(
                f"/api/individuals/{other['id']}/link-sample",
                json={"sample_id": sample1_id},
            )
            assert resp.status_code == 409
            detail = resp.json()["detail"]
            assert detail["individual_id"] == owner["id"]
            assert detail["individual_display_name"] == "Owner"
            assert detail["sample_id"] == sample1_id

        # Owner still owns the link; no leakage to A or B.
        owner_detail = individuals_client.get(f"/api/individuals/{owner['id']}").json()
        assert [s["id"] for s in owner_detail["linked_samples"]] == [sample1_id]
        for other in (other_a, other_b):
            other_detail = individuals_client.get(f"/api/individuals/{other['id']}").json()
            assert other_detail["linked_samples"] == []

    # ── Aggregated-findings dedup edges (Plan §9.5) ────────────────

    def test_aggregated_findings_dedup_across_three_samples_same_rsid(
        self, individuals_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """3-way dedup: same rsid in 3 linked sample DBs → count == 1;
        ``linked_samples`` provenance lists all 3 contributing samples
        (the source data the frontend renders provenance chips from).
        """
        sample1_id = individuals_client.sample1_id  # type: ignore[attr-defined]
        sample2_id = individuals_client.sample2_id  # type: ignore[attr-defined]

        # Seed a 3rd sample whose only high-confidence finding is also
        # rs12345 — the same shared rsid the fixture already wrote to
        # samples 1 and 2. (Fixture also seeds rs67890 on sample 1 and
        # rs99999 on sample 2, so test the count edge by linking only
        # this triplet plus the existing rsids → expected count = 3.)
        sample3_id = _seed_extra_sample(
            tmp_data_dir,
            name="Third Source",
            db_path="samples/sample_3.db",
            file_format="merged_v1",
            file_hash="hash_three",
            findings_rows=[
                {"rsid": "rs12345", "chrom": "1", "pos": 100, "evidence_level": 4},
            ],
        )

        ind = individuals_client.post("/api/individuals", json={"display_name": "Triplet"}).json()
        for sid in (sample1_id, sample2_id, sample3_id):
            resp = individuals_client.post(
                f"/api/individuals/{ind['id']}/link-sample",
                json={"sample_id": sid},
            )
            assert resp.status_code == 200

        detail = individuals_client.get(f"/api/individuals/{ind['id']}").json()
        # Three sample provenance entries, but only 3 distinct rsids
        # across them (rs12345 shared 3-way, rs67890 unique to sample 1,
        # rs99999 unique to sample 2). rs12345 emits once.
        assert sorted(s["id"] for s in detail["linked_samples"]) == sorted(
            [sample1_id, sample2_id, sample3_id]
        )
        assert detail["aggregated_findings_count"] == 3

    def test_aggregated_findings_excludes_low_evidence(
        self, individuals_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """``evidence_level < 3`` rows are excluded from the aggregate."""
        sample_id = _seed_extra_sample(
            tmp_data_dir,
            name="Low-Evidence Source",
            db_path="samples/sample_lowev.db",
            file_format="23andme_v5",
            file_hash="hash_lowev",
            findings_payloads=[
                # Two below-threshold rows (must NOT count).
                {
                    "module": "traits",
                    "evidence_level": 1,
                    "rsid": "rs_low_a",
                    "finding_text": "rs_low_a trait",
                },
                {
                    "module": "traits",
                    "evidence_level": 2,
                    "rsid": "rs_low_b",
                    "finding_text": "rs_low_b trait",
                },
                # One at-threshold row (must count).
                {
                    "module": "cancer",
                    "evidence_level": 3,
                    "rsid": "rs_high",
                    "finding_text": "rs_high finding",
                },
            ],
        )

        ind = individuals_client.post(
            "/api/individuals", json={"display_name": "Low-evidence carrier"}
        ).json()
        resp = individuals_client.post(
            f"/api/individuals/{ind['id']}/link-sample",
            json={"sample_id": sample_id},
        )
        assert resp.status_code == 200

        detail = individuals_client.get(f"/api/individuals/{ind['id']}").json()
        # Only the evidence_level=3 row contributes.
        assert detail["aggregated_findings_count"] == 1

    def test_aggregated_findings_null_rsid_counts_per_sample(
        self, individuals_client: TestClient, tmp_data_dir: Path
    ) -> None:
        """Plan §9.5 carve-out: findings without an rsid (haplogroup /
        pathway-level) count individually per sample rather than being
        deduplicated by rsid (NULL would collapse them all into one).
        """
        # Two samples each carrying ONE NULL-rsid high-confidence finding
        # (haplogroup-style). Expected count = 2 (one per sample), not 1.
        sample_a = _seed_extra_sample(
            tmp_data_dir,
            name="Haplogroup A",
            db_path="samples/sample_haplo_a.db",
            file_format="23andme_v5",
            file_hash="hash_haplo_a",
            findings_payloads=[
                {
                    "module": "haplogroup",
                    "evidence_level": 4,
                    "rsid": None,
                    "haplogroup": "H1a",
                    "finding_text": "Maternal haplogroup H1a",
                },
            ],
        )
        sample_b = _seed_extra_sample(
            tmp_data_dir,
            name="Haplogroup B",
            db_path="samples/sample_haplo_b.db",
            file_format="ancestrydna_v2.0",
            file_hash="hash_haplo_b",
            findings_payloads=[
                {
                    "module": "haplogroup",
                    "evidence_level": 4,
                    "rsid": None,
                    "haplogroup": "R1b",
                    "finding_text": "Paternal haplogroup R1b",
                },
            ],
        )

        ind = individuals_client.post(
            "/api/individuals", json={"display_name": "Haplogroup carrier"}
        ).json()
        for sid in (sample_a, sample_b):
            individuals_client.post(
                f"/api/individuals/{ind['id']}/link-sample",
                json={"sample_id": sid},
            )

        detail = individuals_client.get(f"/api/individuals/{ind['id']}").json()
        # Both haplogroup findings count — no rsid-based dedup collapses
        # them. linked_samples carries the per-sample provenance.
        assert detail["aggregated_findings_count"] == 2
        assert sorted(s["id"] for s in detail["linked_samples"]) == sorted([sample_a, sample_b])


# ── (e) Step 67 / MRG-03 — merge preview route smoke surface ─────────


class TestMergePreviewRoute:
    """Route plumbing for ``POST /api/individuals/{id}/merge/preview``.

    Validates Plan §10.6 routing: the route exists, FastAPI's pydantic
    layer rejects mis-shaped bodies before the service runs, the route
    surfaces 404 / 422 from the individual + service layers. Happy-path
    payload assertion (concordance counts + ``est_duration_seconds``)
    against a hand-curated fixture is the dual-upload fixture's job in
    step 75; that's why this class only covers the routing surface here.
    """

    def test_nonexistent_individual_returns_404(self, individuals_client: TestClient) -> None:
        sample1_id = individuals_client.sample1_id  # type: ignore[attr-defined]
        sample2_id = individuals_client.sample2_id  # type: ignore[attr-defined]
        resp = individuals_client.post(
            "/api/individuals/9999/merge/preview",
            json={
                "source_sample_ids": [sample1_id, sample2_id],
                "strategy": "flag_only",
            },
        )
        assert resp.status_code == 404
        assert "9999" in resp.json()["detail"]

    def test_invalid_strategy_returns_422(self, individuals_client: TestClient) -> None:
        ind = individuals_client.post("/api/individuals", json={"display_name": "Owner"}).json()
        sample1_id = individuals_client.sample1_id  # type: ignore[attr-defined]
        sample2_id = individuals_client.sample2_id  # type: ignore[attr-defined]
        resp = individuals_client.post(
            f"/api/individuals/{ind['id']}/merge/preview",
            json={
                "source_sample_ids": [sample1_id, sample2_id],
                "strategy": "bogus_strategy",
            },
        )
        # Pydantic Literal rejects the unknown value before the service runs.
        assert resp.status_code == 422

    def test_wrong_source_count_returns_422(self, individuals_client: TestClient) -> None:
        ind = individuals_client.post("/api/individuals", json={"display_name": "Owner"}).json()
        sample1_id = individuals_client.sample1_id  # type: ignore[attr-defined]
        resp = individuals_client.post(
            f"/api/individuals/{ind['id']}/merge/preview",
            json={
                "source_sample_ids": [sample1_id],
                "strategy": "flag_only",
            },
        )
        # min_length=2 on the pydantic model rejects the single-id list.
        assert resp.status_code == 422

    def test_samples_not_linked_to_individual_return_422(
        self, individuals_client: TestClient
    ) -> None:
        # The fixture's two seeded samples are unlinked (individual_id is
        # NULL). Asking to preview-merge them against a freshly-created
        # individual surfaces the §10.5 step-1 membership failure as 422.
        ind = individuals_client.post("/api/individuals", json={"display_name": "Owner"}).json()
        sample1_id = individuals_client.sample1_id  # type: ignore[attr-defined]
        sample2_id = individuals_client.sample2_id  # type: ignore[attr-defined]
        resp = individuals_client.post(
            f"/api/individuals/{ind['id']}/merge/preview",
            json={
                "source_sample_ids": [sample1_id, sample2_id],
                "strategy": "flag_only",
            },
        )
        assert resp.status_code == 422
        assert "not linked" in resp.json()["detail"]


class TestAggregateFindingsCountGatesDisclosure:
    """#388 — a gated module's high-confidence finding must not shift
    ``aggregated_findings_count`` before its disclosure gate is acknowledged.

    The aggregate is a per-sample union of evidence>=3 findings. A gated finding
    (APOE #222, sex-aneuploidy #299, Parkinson's #298 — all evidence>=3) leaked a +1
    count delta that re-opened the gated disclosure as a weak signal, even though the
    dedicated routes and /api/analysis/findings withhold it until acknowledged. The
    count must mirror that gate posture (per sample, since gate ack is per sample).
    """

    def _sample1_engine(self, client: TestClient) -> sa.Engine:
        return sa.create_engine(f"sqlite:///{client.data_dir / 'samples/sample_1.db'}")

    def _add_gated_apoe_finding(self, client: TestClient) -> None:
        # A high-confidence APOE (gated) finding on sample 1, which the fixture
        # already seeded with two non-gated rsids (rs12345, rs67890).
        engine = self._sample1_engine(client)
        with engine.begin() as conn:
            conn.execute(
                findings_table.insert(),
                [
                    {
                        "module": "apoe",
                        "evidence_level": 4,
                        "rsid": "rs429358",
                        "finding_text": "APOE diplotype — gated disclosure",
                    }
                ],
            )
        engine.dispose()

    def _acknowledge_apoe_gate(self, client: TestClient) -> None:
        engine = self._sample1_engine(client)
        with engine.begin() as conn:
            conn.execute(apoe_gate.insert().values(id=1, acknowledged=True))
        engine.dispose()

    def test_gated_finding_excluded_from_count_until_acknowledged(
        self, individuals_client: TestClient
    ) -> None:
        client = individuals_client
        sample1_id = client.sample1_id  # type: ignore[attr-defined]
        self._add_gated_apoe_finding(client)

        ind_id = client.post("/api/individuals", json={"display_name": "Subject"}).json()["id"]
        body = client.post(
            f"/api/individuals/{ind_id}/link-sample",
            json={"sample_id": sample1_id},
        ).json()
        # Two non-gated rsids count; the APOE finding (rs429358) is withheld pre-ack.
        assert body["aggregated_findings_count"] == 2

        # After acknowledging the APOE gate for this sample, it counts.
        self._acknowledge_apoe_gate(client)
        body = client.get(f"/api/individuals/{ind_id}").json()
        assert body["aggregated_findings_count"] == 3
